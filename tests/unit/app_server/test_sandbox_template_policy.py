"""Organization isolation and backward-compatible conversation launch choices."""

import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from starlette.datastructures import State

from integrations.resolver_context import ResolverUserContext
from openhands.app_server import config as app_config
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartRequest,
    AppConversationStartTask,
)
from openhands.app_server.app_conversation.live_status_app_conversation_service import (
    LiveStatusAppConversationService,
)
from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.preset_sandbox_spec_service import (
    PresetSandboxSpecService,
)
from openhands.app_server.sandbox.sandbox_models import SandboxStatus
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.sandbox.sandbox_spec_router import (
    get_sandbox_selection_options,
)
from openhands.app_server.sandbox.sandbox_spec_service import resolve_sandbox_spec
from openhands.app_server.sandbox.sandbox_template_policy import (
    SandboxTemplatePolicy,
    ScopedSandboxSpecService,
)
from openhands.app_server.user.auth_user_context import (
    AuthUserContext,
    AuthUserContextInjector,
)
from server.services.feature_flag_service import feature_flag_service
from server.services.sandbox_template_policy import (
    ORG_TEMPLATES_ENV,
    SANDBOX_SELECTOR_FLAG,
    get_org_template_policy,
)


def scoped(*ids, default='standard', enabled=True):
    return ScopedSandboxSpecService(
        PresetSandboxSpecService(
            [
                SandboxSpecInfo(id=id_, display_name=id_.title(), command=None)
                for id_ in ['standard', 'large', 'testing']
            ]
        ),
        SandboxTemplatePolicy(
            template_ids=list(ids),
            default_template_id=default,
            selector_enabled=enabled,
        ),
    )


@pytest.mark.asyncio
async def test_org_choices_enforce_starts_without_breaking_existing_metadata():
    org_a = scoped('standard', 'large')
    org_b = scoped('testing', default='testing')
    assert [s.id for s in (await get_sandbox_selection_options(org_a)).items] == [
        'standard',
        'large',
    ]
    assert [s.id for s in (await get_sandbox_selection_options(org_b)).items] == [
        'testing'
    ]
    assert await org_b.get_start_sandbox_spec('large') is None
    assert (await org_b.batch_get_sandbox_specs(['large']))[0].id == 'large'
    # Old conversations may still need the original template for archival.
    assert (await org_b.get_sandbox_spec('large')).id == 'large'
    resolved = await resolve_sandbox_spec(
        None, 'large', org_b, logging.getLogger(__name__)
    )
    assert resolved.id == 'testing'
    with pytest.raises(ValueError, match='not found'):
        await resolve_sandbox_spec('large', None, org_b, logging.getLogger(__name__))


@pytest.mark.asyncio
async def test_disabled_flag_hides_choices_but_keeps_org_default():
    service = scoped('standard', enabled=False)
    assert (await get_sandbox_selection_options(service)).model_dump() == {
        'enabled': False,
        'items': [],
    }
    assert (await service.get_default_sandbox_spec()).id == 'standard'


@pytest.mark.asyncio
async def test_org_policy_pagination():
    service = scoped('standard', 'large', 'testing')
    first = await service.search_sandbox_specs(limit=1)
    second = await service.search_sandbox_specs(first.next_page_id, limit=1)
    assert [s.id for s in second.items] == ['large']
    assert second.next_page_id == '2'


@pytest.mark.asyncio
async def test_helm_policy_and_flag_use_only_authenticated_org(monkeypatch):
    org_a, org_b = uuid4(), uuid4()
    monkeypatch.setenv(
        ORG_TEMPLATES_ENV,
        json.dumps(
            {
                str(org_a): {'template_ids': ['large'], 'default_template_id': 'large'},
                str(org_b): {
                    'template_ids': ['testing'],
                    'default_template_id': 'testing',
                },
            }
        ),
    )
    evaluate = AsyncMock(return_value=True)
    monkeypatch.setattr(feature_flag_service, 'is_enabled', evaluate)
    context = AuthUserContext(
        user_auth=SimpleNamespace(get_effective_org_id=AsyncMock(return_value=org_a))
    )
    assert (await context.get_sandbox_template_policy()).template_ids == ['large']
    assert (await get_org_template_policy(org_b)).template_ids == ['testing']
    evaluate.assert_awaited_with(SANDBOX_SELECTOR_FLAG, org_id=str(org_b))
    assert await get_org_template_policy(uuid4()) is None

    # The real request-scoping entry point must wrap (not mutate/cache org
    # state on) the shared discovery service for every organization.
    @asynccontextmanager
    async def discovery_context(*args):
        yield scoped('standard').delegate

    configuration = SimpleNamespace(
        sandbox_spec=SimpleNamespace(context=discovery_context),
        user=AuthUserContextInjector(),
    )
    monkeypatch.setattr(app_config, 'get_global_config', lambda: configuration)
    for org_id, expected_id in [(org_a, 'large'), (org_b, 'testing')]:
        auth = SimpleNamespace(get_effective_org_id=AsyncMock(return_value=org_id))
        state = State({'user_context': AuthUserContext(user_auth=auth)})
        async with app_config.get_sandbox_spec_service(state) as service:
            result = await get_sandbox_selection_options(service)
        assert [item.id for item in result.items] == [expected_id]


@pytest.mark.asyncio
@pytest.mark.parametrize('selector_enabled', [False, True])
async def test_integration_resolver_enforces_org_policy_when_starting_sandboxes(
    monkeypatch, selector_enabled
):
    org_id = uuid4()
    monkeypatch.setenv(
        ORG_TEMPLATES_ENV,
        json.dumps(
            {str(org_id): {'template_ids': ['large'], 'default_template_id': 'large'}}
        ),
    )
    monkeypatch.setattr(
        feature_flag_service, 'is_enabled', AsyncMock(return_value=selector_enabled)
    )
    # Integration routing, rather than the user's currently active organization,
    # owns the policy used for this conversation.
    auth = SimpleNamespace(get_effective_org_id=AsyncMock(return_value=uuid4()))
    context = ResolverUserContext(saas_user_auth=auth, resolver_org_id=org_id)

    @asynccontextmanager
    async def discovery_context(*args):
        yield scoped('standard').delegate

    configuration = SimpleNamespace(
        sandbox_spec=SimpleNamespace(context=discovery_context),
        user=AuthUserContextInjector(),
    )
    monkeypatch.setattr(app_config, 'get_global_config', lambda: configuration)
    state = State({'user_context': context})
    async with app_config.get_sandbox_spec_service(state) as service:
        assert service.selection_enabled is selector_enabled
        assert service.allow_sandbox_reuse is False
        assert (await service.get_default_sandbox_spec()).id == 'large'
        # Ignore an out-of-policy saved preference, but reject an explicit one.
        resolved = await resolve_sandbox_spec(
            None, 'testing', service, logging.getLogger(__name__)
        )
        assert resolved.id == 'large'
        with pytest.raises(ValueError, match="Sandbox Spec 'testing' not found"):
            await resolve_sandbox_spec(
                'testing', None, service, logging.getLogger(__name__)
            )


@pytest.mark.parametrize(
    'value,expected', [('true', True), ('1', True), ('false', False), ('0', False)]
)
def test_selector_env_default(value, expected, monkeypatch):
    monkeypatch.setenv(SANDBOX_SELECTOR_FLAG, value)
    assert feature_flag_service.resolve_env_default(SANDBOX_SELECTOR_FLAG) is expected


@pytest.mark.parametrize(
    'ids,default',
    [
        ([], 'standard'),
        (['large'], 'standard'),
        (['large', 'large'], 'large'),
        ([' large'], ' large'),
    ],
)
def test_invalid_org_configuration_is_rejected(ids, default):
    with pytest.raises(ValidationError):
        SandboxTemplatePolicy(template_ids=ids, default_template_id=default)


def test_old_requests_still_work_and_conflicting_overrides_are_rejected():
    assert AppConversationStartRequest().sandbox_spec_id is None
    with pytest.raises(ValidationError, match='cannot be combined'):
        AppConversationStartRequest(sandbox_id='existing', sandbox_spec_id='large')
    with pytest.raises(ValidationError, match='child conversations inherit'):
        AppConversationStartRequest(
            parent_conversation_id=uuid4(), sandbox_spec_id='large'
        )


@pytest.mark.parametrize(
    'value',
    [
        '{invalid json',
        '{"not-a-uuid": {}}',
        json.dumps(
            {
                '11111111-1111-4111-8111-111111111111': {
                    'template_ids': ['standard'],
                    'default_template_id': 'forbidden',
                }
            }
        ),
    ],
)
def test_invalid_org_config_rejects_app_bootstrap_before_requests(monkeypatch, value):
    monkeypatch.setenv(ORG_TEMPLATES_ENV, value)
    with pytest.raises(ValidationError):
        app_config.config_from_env()


@pytest.mark.asyncio
async def test_flag_store_outage_keeps_org_policy_with_selector_disabled(monkeypatch):
    org = uuid4()
    monkeypatch.setenv(
        ORG_TEMPLATES_ENV,
        json.dumps(
            {
                str(org): {
                    'template_ids': ['standard'],
                    'default_template_id': 'standard',
                }
            }
        ),
    )
    monkeypatch.setattr(
        feature_flag_service,
        'is_enabled',
        AsyncMock(side_effect=ConnectionError('unavailable')),
    )
    policy = await get_org_template_policy(org)
    assert policy.selector_enabled is False
    assert policy.template_ids == ['standard']
    service = ScopedSandboxSpecService(scoped('standard').delegate, policy)
    assert (await service.get_default_sandbox_spec()).id == 'standard'
    assert await service.get_start_sandbox_spec('large') is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'template,enabled,should_start',
    [
        ('large', True, True),
        ('testing', True, False),
        ('large', False, False),
        (None, False, True),
    ],
)
async def test_launch_routes_template_and_rejects_forbidden_choices(
    template, enabled, should_start
):
    service = Mock()
    service.sandbox_spec_service = scoped('standard', 'large', enabled=enabled)
    service._find_running_sandbox_for_user = AsyncMock()
    sandbox = Mock(id='new', status=SandboxStatus.RUNNING)
    service.sandbox_service.start_sandbox = AsyncMock(return_value=sandbox)
    service.sandbox_service.wait_for_sandbox_running = AsyncMock()
    task = AppConversationStartTask(
        created_by_user_id='test-user',
        request=AppConversationStartRequest(sandbox_spec_id=template),
    )

    async def run():
        async for _ in LiveStatusAppConversationService._wait_for_sandbox_start(
            service, task
        ):
            pass

    if should_start:
        await run()
        expected = {'sandbox_id': None}
        if template is not None:
            expected['sandbox_spec_id'] = template
        service.sandbox_service.start_sandbox.assert_awaited_once_with(**expected)
        service._find_running_sandbox_for_user.assert_not_awaited()
    else:
        with pytest.raises(SandboxError):
            await run()
        service.sandbox_service.start_sandbox.assert_not_awaited()
