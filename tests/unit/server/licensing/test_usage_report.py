"""The usage report: what it counts, and how it sends."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.app_server.config import get_global_config
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.sandbox.docker_sandbox_service import (
    DockerSandboxServiceInjector,
)
from openhands.app_server.sandbox.e2b_sandbox_service import E2BSandboxServiceInjector
from openhands.app_server.sandbox.k8s_agent_sandbox_service import (
    K8sAgentSandboxServiceInjector,
)
from openhands.app_server.sandbox.process_sandbox_service import (
    ProcessSandboxServiceInjector,
)
from openhands.app_server.sandbox.remote_sandbox_service import (
    RemoteSandboxServiceInjector,
)
from openhands.app_server.utils.logger import openhands_logger
from openhands.app_server.web_client.default_web_client_config_injector import (
    DefaultWebClientConfigInjector,
)
from openhands.app_server.web_client.web_client_models import WebClientFeatureFlags
from server.licensing import sso, usage_report
from server.licensing.usage_report import UsageReport, sandbox_strategy
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import (
    AuthProviderDetails,
    OAuthProviderDetails,
    PasswordProviderDetails,
    SamlProviderDetails,
)
from storage.instance_settings import InstanceSettings
from storage.stored_conversation_metadata import StoredConversationMetadata

LICENSE_KEY = 'ohe_lic_test'


class FakeAutomationService:
    """Stand in for the automation service's ``GET /v1/usage``."""

    def __init__(self, response: httpx.Response | Exception):
        self.response = response
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeCustomerCenter:
    def __init__(self, response: httpx.Response | Exception):
        self.response = response
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def report_db(async_session_maker, oauth_v2_stores):
    with (
        patch('server.licensing.usage_report.a_session_maker', async_session_maker),
        patch('storage.instance_settings_store.a_session_maker', async_session_maker),
    ):
        yield async_session_maker


@pytest.fixture
def docker_sandbox(monkeypatch):
    monkeypatch.setattr(get_global_config(), 'sandbox', DockerSandboxServiceInjector())


@pytest.fixture
def personal_workspaces(monkeypatch):
    def set_hidden(hidden: bool) -> None:
        monkeypatch.setattr(
            get_global_config(),
            'web_client',
            DefaultWebClientConfigInjector(
                feature_flags=WebClientFeatureFlags(hide_personal_workspaces=hidden)
            ),
        )

    set_hidden(False)
    return set_hidden


@pytest.fixture
def no_sso(monkeypatch, oauth_v2_login_off):
    monkeypatch.setattr(usage_report, 'ENABLE_ENTERPRISE_SSO', '')


@pytest.fixture
def automation_service(monkeypatch):
    """An automation service configured with the shared service key.

    Returns a function that sets its response and gives back the fake, with
    the requests it receives. ``automation_count`` builds its own client, so
    the fake is installed as the client's transport.
    """
    monkeypatch.setattr(
        usage_report, 'AUTOMATION_SERVICE_URL', 'http://automation/api/automation'
    )
    monkeypatch.setenv('AUTOMATIONS_SERVICE_KEY', 'shared-key')
    real_async_client = httpx.AsyncClient

    def respond(response: httpx.Response | Exception) -> FakeAutomationService:
        fake = FakeAutomationService(response)
        monkeypatch.setattr(
            httpx,
            'AsyncClient',
            lambda **kwargs: real_async_client(
                transport=httpx.MockTransport(fake.handler)
            ),
        )
        return fake

    return respond


def _report(**overrides) -> UsageReport:
    values: dict = {
        'license_key': LICENSE_KEY,
        'instance_id': 'instance-1',
        'conversations': 3,
        'users': 2,
        'automations': None,
        'orgs': 1,
        'sso_enabled': False,
        'personal_workspaces_enabled': True,
        'sandbox_strategy': 'docker',
    }
    values.update(overrides)
    return UsageReport(**values)


async def _add_conversations(session_maker, count: int, **kwargs) -> None:
    async with session_maker() as session:
        for i in range(count):
            session.add(
                StoredConversationMetadata(conversation_id=f'conv-{i}', **kwargs)
            )
        await session.commit()


async def _add_provider(
    session_maker, details: AuthProviderDetails, *, enabled=True, is_idp=True
) -> None:
    async with session_maker() as session:
        session.add(
            AuthProvider(
                title='provider', is_idp=is_idp, enabled=enabled, details=details
            )
        )
        await session.commit()


def _oidc(**overrides) -> OAuthProviderDetails:
    values: dict = {
        'provider_type': ProviderType.ENTERPRISE_SSO,
        'client_id': 'okta-client',
        'client_secret': SecretStr('secret'),
        'authorization_url': 'https://okta.example/authorize',
    }
    values.update(overrides)
    return OAuthProviderDetails(**values)


# Collecting


@pytest.mark.usefixtures('docker_sandbox', 'personal_workspaces', 'no_sso')
async def test_reports_lifetime_totals_and_settings(report_db, create_user, create_org):
    # Two users, each with a personal workspace, and one team org.
    for _ in range(2):
        personal = create_org()
        create_user(id=personal.id, current_org_id=personal.id)
    create_org(name='Team')
    await _add_conversations(report_db, 2)
    # A deleted conversation still happened.
    async with report_db() as session:
        session.add(
            StoredConversationMetadata(
                conversation_id='deleted', deleted_at=datetime.now(UTC)
            )
        )
        await session.commit()

    report = await usage_report.collect_usage_report(LICENSE_KEY)

    async with report_db() as session:
        settings = await session.get(InstanceSettings, 1)
    assert settings is not None
    assert report == UsageReport(
        license_key=LICENSE_KEY,
        instance_id=str(settings.instance_id),
        conversations=3,
        users=2,
        automations=None,
        orgs=1,
        sso_enabled=False,
        personal_workspaces_enabled=True,
        sandbox_strategy='docker',
    )


@pytest.mark.usefixtures('docker_sandbox', 'no_sso')
async def test_every_report_carries_the_same_instance_id(
    report_db, personal_workspaces
):
    first = await usage_report.collect_usage_report(LICENSE_KEY)
    second = await usage_report.collect_usage_report(LICENSE_KEY)

    assert first is not None and second is not None
    assert first.instance_id == second.instance_id


@pytest.mark.usefixtures('docker_sandbox', 'no_sso')
async def test_reports_personal_workspaces_off_when_the_web_client_hides_them(
    report_db, personal_workspaces
):
    personal_workspaces(True)

    report = await usage_report.collect_usage_report(LICENSE_KEY)

    assert report is not None
    assert report.personal_workspaces_enabled is False


@pytest.mark.parametrize(
    ('injector', 'expected'),
    [
        (DockerSandboxServiceInjector(), 'docker'),
        (ProcessSandboxServiceInjector(), 'process'),
        (
            RemoteSandboxServiceInjector(api_url='https://r.example', api_key='k'),
            'remote',
        ),
        (E2BSandboxServiceInjector(), 'e2b'),
        (K8sAgentSandboxServiceInjector(), 'k8s-agent-sandbox'),
        (None, None),
    ],
)
def test_names_the_sandbox_service_in_use(injector, expected):
    assert sandbox_strategy(injector) == expected


@pytest.mark.usefixtures('personal_workspaces', 'no_sso')
async def test_sends_nothing_for_a_sandbox_customer_center_does_not_know(
    report_db, monkeypatch
):
    monkeypatch.setattr(get_global_config(), 'sandbox', None)

    assert await usage_report.collect_usage_report(LICENSE_KEY) is None


# SSO


async def test_sso_is_on_with_the_keycloak_saml_provider(
    report_db, monkeypatch, oauth_v2_login_off
):
    monkeypatch.setattr(usage_report, 'ENABLE_ENTERPRISE_SSO', 'true')

    async with report_db() as session:
        assert await usage_report.sso_enabled(session) is True


async def test_sso_is_off_without_it_on_keycloak_login(report_db, no_sso):
    async with report_db() as session:
        assert await usage_report.sso_enabled(session) is False


@pytest.mark.parametrize(
    'details',
    [
        SamlProviderDetails(sso_url='https://idp.example/sso'),
        _oidc(),
    ],
    ids=['saml', 'oidc'],
)
async def test_sso_is_on_with_an_enabled_sso_login_provider(
    report_db, monkeypatch, oauth_v2_login_on, details
):
    monkeypatch.setattr(usage_report, 'ENABLE_ENTERPRISE_SSO', '')
    await _add_provider(report_db, details)

    async with report_db() as session:
        assert await usage_report.sso_enabled(session) is True


@pytest.mark.parametrize(
    ('details', 'enabled', 'is_idp'),
    [
        (_oidc(), False, True),
        (_oidc(), True, False),
        (PasswordProviderDetails(), True, True),
        (_oidc(provider_type=ProviderType.GITHUB), True, True),
        (_oidc(legacy_keycloak_type=ProviderType.GITHUB), True, True),
        (_oidc(client_id='keycloak-client'), True, True),
    ],
    ids=['disabled', 'not-login', 'password', 'git', 'keycloak-brand', 'keycloak'],
)
async def test_sso_is_off_with_only_other_login_providers(
    report_db, monkeypatch, oauth_v2_login_on, details, enabled, is_idp
):
    monkeypatch.setattr(usage_report, 'ENABLE_ENTERPRISE_SSO', '')
    monkeypatch.setattr(sso, 'KEYCLOAK_CLIENT_ID', 'keycloak-client')
    await _add_provider(report_db, details, enabled=enabled, is_idp=is_idp)

    async with report_db() as session:
        assert await usage_report.sso_enabled(session) is False


# Automations


@pytest.mark.usefixtures('docker_sandbox', 'personal_workspaces', 'no_sso')
async def test_reports_the_automation_count(report_db, automation_service):
    service = automation_service(httpx.Response(200, json={'automations': 3}))

    report = await usage_report.collect_usage_report(LICENSE_KEY)

    assert report is not None
    assert report.automations == 3
    [request] = service.requests
    assert str(request.url) == 'http://automation/api/automation/v1/usage'
    assert request.headers['X-Service-API-Key'] == 'shared-key'


@pytest.mark.parametrize(
    'response',
    [
        httpx.Response(404),
        httpx.Response(401),
        httpx.ConnectError('refused'),
        httpx.Response(200, json={'automations': -1}),
        httpx.Response(200, text='not json'),
    ],
    ids=['older-service', 'key-mismatch', 'unreachable', 'negative', 'not-json'],
)
async def test_reports_no_automation_count_when_the_service_cannot_answer(
    automation_service, response, caplog
):
    automation_service(response)

    with caplog.at_level(logging.WARNING, logger=openhands_logger.name):
        count = await usage_report.automation_count()

    assert count is None
    assert any(
        'usage_report_automation_count_failed' in r.getMessage() for r in caplog.records
    )


@pytest.mark.parametrize(
    ('url', 'key'),
    [('', 'shared-key'), ('http://automation/api/automation', '')],
    ids=['no-service-url', 'no-service-key'],
)
async def test_reports_no_automation_count_without_a_service(monkeypatch, url, key):
    monkeypatch.setattr(usage_report, 'AUTOMATION_SERVICE_URL', url)
    monkeypatch.setenv('AUTOMATIONS_SERVICE_KEY', key)

    assert await usage_report.automation_count() is None


# Sending


async def test_posts_the_report_to_customer_center():
    customer_center = FakeCustomerCenter(httpx.Response(204))

    sent = await usage_report.send_usage_report(
        _report(), 'https://cc.example', customer_center.client
    )

    assert sent is True
    [request] = customer_center.requests
    assert request.method == 'POST'
    assert str(request.url) == 'https://cc.example/api/v1/usage-reports'
    assert json.loads(request.content) == _report().model_dump()


@pytest.mark.parametrize(
    ('response', 'logged'),
    [
        (httpx.Response(401), 'key not valid'),
        (httpx.Response(400, text='users: Expected number'), 'Expected number'),
        (httpx.Response(503), '503'),
        (httpx.ConnectError('refused'), 'ConnectError'),
    ],
    ids=['revoked-key', 'bad-field', 'server-error', 'unreachable'],
)
async def test_logs_a_failed_report_without_raising(response, logged, caplog):
    customer_center = FakeCustomerCenter(response)

    with caplog.at_level(logging.WARNING, logger=openhands_logger.name):
        sent = await usage_report.send_usage_report(
            _report(), 'https://cc.example', customer_center.client
        )

    assert sent is False
    [record] = [r for r in caplog.records if 'usage_report_failed' in r.getMessage()]
    assert logged in f'{record.getMessage()} {record.__dict__}'


async def test_run_does_nothing_without_a_license_key(monkeypatch):
    monkeypatch.delenv('OPENHANDS_LICENSE_KEY', raising=False)
    customer_center = FakeCustomerCenter(httpx.Response(204))

    await usage_report.run(http_client_factory=customer_center.client)

    assert customer_center.requests == []


async def test_run_does_nothing_with_an_offline_license(monkeypatch):
    monkeypatch.setenv('OPENHANDS_LICENSE_KEY', LICENSE_KEY)
    monkeypatch.setenv('OPENHANDS_OFFLINE_LICENSE', '{}')
    customer_center = FakeCustomerCenter(httpx.Response(204))

    await usage_report.run(http_client_factory=customer_center.client)

    assert customer_center.requests == []


@pytest.mark.usefixtures('docker_sandbox', 'personal_workspaces', 'no_sso')
async def test_run_sends_a_report_to_the_license_server(report_db, monkeypatch):
    monkeypatch.setenv('OPENHANDS_LICENSE_KEY', LICENSE_KEY)
    monkeypatch.setenv('LICENSE_SERVER_URL', 'https://cc.example/')
    monkeypatch.delenv('LICENSE_ENFORCEMENT_ENABLED', raising=False)
    monkeypatch.delenv('OPENHANDS_OFFLINE_LICENSE', raising=False)
    monkeypatch.delenv('OPENHANDS_OFFLINE_LICENSE_FILE', raising=False)
    customer_center = FakeCustomerCenter(httpx.Response(204))

    await usage_report.run(http_client_factory=customer_center.client)

    [request] = customer_center.requests
    assert str(request.url) == 'https://cc.example/api/v1/usage-reports'
    assert json.loads(request.content)['license_key'] == LICENSE_KEY
