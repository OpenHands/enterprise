"""The secret-lookups service route and the org-bound references it signs.

Real PostgreSQL rows back the users, memberships and custom secrets. Only the
git providers are stubbed: a connected provider's token comes from
``SaasUserAuth.get_provider_tokens``, which talks to the provider in production.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets as pysecrets
import socket
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import jwt as pyjwt
import pytest
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import SecretStr, ValidationError

from openhands.app_server.config import get_global_config
from openhands.app_server.event_callback import webhook_router
from openhands.app_server.event_callback.webhook_router import get_secret
from openhands.app_server.integrations.provider import ProviderToken
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.sandbox.docker_sandbox_service import (
    DockerSandboxServiceInjector,
)
from openhands.app_server.sandbox.remote_sandbox_service import (
    RemoteSandboxServiceInjector,
)
from openhands.app_server.services.jwt_service import JwtAudience, JwtService
from openhands.app_server.shared import server_config
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth.authorization import get_user_super_role, is_instance_super_admin
from server.auth.org_access import assert_org_usable_for_product
from server.auth.saas_user_auth import SaasUserAuth
from server.routes import service as service_routes
from server.routes.service import (
    SecretLookupsRequest,
    create_secret_lookups,
    jwt_dependency,
    service_router,
)
from server.services import secret_lookup_service
from server.services.secret_lookup_service import sandbox_secret_lookup_url
from storage.auth_provider import AuthProvider
from storage.auth_provider_details import OAuthProviderDetails
from storage.auth_provider_store import AuthProviderStore
from storage.oauth_token_store import OAuthTokenStore
from storage.org_member import OrgMember
from storage.org_secrets_store import OrgSecretsStore
from storage.role import Role
from storage.saas_secrets_store import SaasSecretsStore
from storage.user import User
from tests.unit.private_git_server import PrivateGitServer, make_bare_repo
from tests.unit.unscoped_jws import mint_unscoped_jws_token

SERVICE_KEY = 'fake-service-key'
WEB_URL = 'https://app.example.com'
LOOKUP_URL = f'{WEB_URL}/api/v1/webhooks/secrets'
JWT = JwtService(
    keys=[EncryptionKey(kid='test', key=SecretStr('fake-test-key'), active=True)]
)

# Every value a test stores. None may appear in a response body or a log line,
# except a lookup endpoint's body for the one secret it resolves.
VALUES = {
    'PERSONAL_PAT': 'fake-personal-value-a',
    'SHARED_PAT': 'fake-shared-value-a',
    'B_ONLY_PAT': 'fake-personal-value-b',
    'GITHUB_TOKEN': 'fake-github-provider-token',
    'PLUGINS_OAUTH': 'fake-oauth-access-token',
    'COLLIDING': 'fake-colliding-custom-value',
    'LOWERCASE_CUSTOM': 'fake-lowercase-custom-value',
}


@pytest.fixture
async def principal(
    monkeypatch,
    async_session_maker,
    session_maker,
    create_org,
    create_user,
):
    """A member of orgs A and B whose current org is B.

    Org A holds a personal secret, an org-shared secret and a personal secret
    named like a provider token; org B holds a personal secret of its own.
    """
    org_a, org_b = create_org(), create_org()
    user = create_user(current_org_id=org_b.id)
    with session_maker() as session:
        role = Role(name='test-member', rank=10)
        session.add(role)
        session.flush()
        for org in (org_a, org_b):
            session.add(
                OrgMember(
                    org_id=org.id, user_id=user.id, role_id=role.id, llm_api_key='fake'
                )
            )
        session.commit()

    for module in (
        'server.auth.saas_user_auth',
        'storage.user_store',
        'storage.org_member_store',
        'storage.org_store',
        'storage.saas_secrets_store',
        'storage.org_secrets_store',
        'storage.auth_provider_store',
        'storage.oauth_token_store',
    ):
        monkeypatch.setattr(f'{module}.a_session_maker', async_session_maker)
    monkeypatch.setattr('storage.encrypt_utils.get_jwt_service', lambda: JWT)
    monkeypatch.setattr(
        server_config, 'user_auth_class', 'server.auth.saas_user_auth.SaasUserAuth'
    )

    async def secrets_store(user_id, effective_org_id=None):
        return SaasSecretsStore(user_id, JWT, effective_org_id=effective_org_id)

    monkeypatch.setattr(SaasSecretsStore, 'get_instance', secrets_store)
    # The system-managed key is provisioned separately; it is never handed out.
    monkeypatch.setattr(
        SaasUserAuth, '_get_openhands_api_key', AsyncMock(return_value='fake-oh-key')
    )
    monkeypatch.setattr(service_routes, 'AUTOMATIONS_SERVICE_KEY', SERVICE_KEY)
    monkeypatch.setattr(service_routes, 'sandbox_secret_lookup_url', lambda: LOOKUP_URL)

    personal_a = SaasSecretsStore(str(user.id), JWT, effective_org_id=org_a.id)
    await personal_a.upsert_personal('PERSONAL_PAT', VALUES['PERSONAL_PAT'])
    await personal_a.upsert_personal('GITHUB_TOKEN', VALUES['COLLIDING'])
    await SaasSecretsStore(
        str(user.id), JWT, effective_org_id=org_b.id
    ).upsert_personal('B_ONLY_PAT', VALUES['B_ONLY_PAT'])
    await OrgSecretsStore(org_a.id, JWT).create_shared(
        name='SHARED_PAT',
        value=VALUES['SHARED_PAT'],
        description=None,
        created_by_user_id=str(user.id),
    )
    return user, org_a, org_b


@pytest.fixture
def github_connected(monkeypatch):
    """The principal has a connected GitHub account (a provider token)."""

    async def provider_tokens(self):
        return MappingProxyType(
            {
                ProviderType.GITHUB: ProviderToken(
                    token=SecretStr(VALUES['GITHUB_TOKEN'])
                )
            }
        )

    async def latest_token(self, provider_type, git_provider_id=None):
        assert provider_type is ProviderType.GITHUB
        assert git_provider_id is None
        return VALUES['GITHUB_TOKEN']

    monkeypatch.setattr(SaasUserAuth, 'get_provider_tokens', provider_tokens)
    monkeypatch.setattr(
        'openhands.app_server.user.auth_user_context.AuthUserContext.get_latest_token',
        latest_token,
    )


@pytest.fixture
async def oauth_named_secret(principal, monkeypatch):
    """An OAuth v2 provider whose token the principal holds as PLUGINS_OAUTH."""
    monkeypatch.setenv('ENABLE_OAUTH_V2_LOGIN', 'true')
    user, _org_a, _org_b = principal
    provider = await AuthProviderStore().create(
        AuthProvider(
            title='Plugins GitLab',
            is_idp=False,
            enabled=True,
            details=OAuthProviderDetails(
                provider_type=ProviderType.GITLAB,
                client_id='cid',
                token_url='https://gitlab.example.com/oauth/token',
                named_secret='PLUGINS_OAUTH',
            ),
        )
    )
    await OAuthTokenStore(user_id=user.id, oauth_provider_id=provider.id).store_tokens(
        access_token=VALUES['PLUGINS_OAUTH'],
        refresh_token='fake-refresh',
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        refresh_token_expires_at=None,
    )
    return provider


async def _sign(user_id, org_id, names, **fields):
    return await create_secret_lookups(
        user_id=str(user_id),
        org_id=org_id,
        request=SecretLookupsRequest(names=names, **fields),
        x_service_api_key=SERVICE_KEY,
        jwt_service=JWT,
    )


def _claims(token: str) -> dict:
    return JWT.verify_jws_token(token, audience=JwtAudience.SECRET_LOOKUP)


async def _resolve(token: str):
    return await get_secret(access_token=token, jwt_service=JWT)


def _assert_no_values(text: str, *, allowed: str | None = None) -> None:
    for value in VALUES.values():
        if value != allowed:
            assert value not in text


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signs_one_reference_per_name_and_lists_the_rest(
    principal, github_connected, oauth_named_secret, caplog
):
    caplog.set_level(logging.INFO)
    user, org_a, _org_b = principal
    before = datetime.now(timezone.utc)

    response = await _sign(
        user.id,
        org_a.id,
        [
            'PERSONAL_PAT',
            'SHARED_PAT',
            'GITHUB_TOKEN',
            'PLUGINS_OAUTH',
            'GITLAB_TOKEN',
            'B_ONLY_PAT',
            'OPENHANDS_API_KEY',
            'NOT_A_SECRET',
            'PERSONAL_PAT',
        ],
        purpose='plugin_source',
        automation_id='6f1c0000-0000-4000-8000-000000000001',
    )

    assert response.lookup_url == LOOKUP_URL
    assert response.lookup_path == '/api/v1/webhooks/secrets'
    assert set(response.secrets) == {
        'PERSONAL_PAT',
        'SHARED_PAT',
        'GITHUB_TOKEN',
        'PLUGINS_OAUTH',
    }
    # GitLab is linked through OAuth v2, so GITLAB_TOKEN gives way to its named
    # secret, as in a conversation; org B's secret is not org A's.
    assert response.missing == [
        'GITLAB_TOKEN',
        'B_ONLY_PAT',
        'OPENHANDS_API_KEY',
        'NOT_A_SECRET',
    ]
    kinds = {name: ref.kind for name, ref in response.secrets.items()}
    assert kinds == {
        'PERSONAL_PAT': 'custom',
        'SHARED_PAT': 'custom',
        # A connected provider wins over the personal secret of the same name.
        'GITHUB_TOKEN': 'provider',
        'PLUGINS_OAUTH': 'oauth',
    }
    assert response.secrets['GITHUB_TOKEN'].provider == 'github'

    tokens = {
        name: ref.headers['X-Access-Token'] for name, ref in response.secrets.items()
    }
    for name, token in tokens.items():
        claims = _claims(token)
        assert claims['user_id'] == str(user.id)
        assert claims['org_id'] == str(org_a.id)
        assert claims['aud'] == JwtAudience.SECRET_LOOKUP.value
        assert claims['exp'] - claims['iat'] == 900
        targets = {'provider_type', 'oauth_provider_id', 'secret_name'} & set(claims)
        assert len(targets) == 1, name
    assert _claims(tokens['PERSONAL_PAT'])['secret_name'] == 'PERSONAL_PAT'
    assert _claims(tokens['GITHUB_TOKEN'])['provider_type'] == 'github'
    assert (
        _claims(tokens['PLUGINS_OAUTH'])['oauth_provider_id'] == oauth_named_secret.id
    )
    assert before + timedelta(seconds=899) <= response.expires_at
    assert response.expires_at <= datetime.now(timezone.utc) + timedelta(seconds=900)

    _assert_no_values(response.model_dump_json())
    _assert_no_values(caplog.text)
    signed = [r for r in caplog.records if r.message == 'service:secret_lookups_signed']
    assert signed[-1].purpose == 'plugin_source'
    assert signed[-1].missing == response.missing


@pytest.mark.asyncio
async def test_a_lowercase_name_is_an_ordinary_custom_secret_name(
    principal, github_connected
):
    # The provider token is only ever GITHUB_TOKEN, so github_token is just a
    # custom secret name, never the GitHub token.
    user, org_a, _org_b = principal
    await SaasSecretsStore(
        str(user.id), JWT, effective_org_id=org_a.id
    ).upsert_personal('github_token', VALUES['LOWERCASE_CUSTOM'])

    response = await _sign(user.id, org_a.id, ['github_token', 'GITHUB_TOKEN'])

    lower = response.secrets['github_token']
    upper = response.secrets['GITHUB_TOKEN']
    assert (lower.kind, lower.provider) == ('custom', None)
    assert _claims(lower.headers['X-Access-Token'])['secret_name'] == 'github_token'
    resolved = await _resolve(lower.headers['X-Access-Token'])
    assert resolved.body.decode() == VALUES['LOWERCASE_CUSTOM']
    # The uppercase name still means the provider token, over org A's custom
    # GITHUB_TOKEN, as at conversation launch.
    assert (upper.kind, upper.provider) == ('provider', 'github')
    resolved = await _resolve(upper.headers['X-Access-Token'])
    assert resolved.body.decode() == VALUES['GITHUB_TOKEN']


@pytest.mark.asyncio
async def test_the_lowercase_name_does_not_alias_the_provider_token(
    principal, github_connected
):
    user, org_a, _org_b = principal

    response = await _sign(user.id, org_a.id, ['github_token'])

    assert response.secrets == {}
    assert response.missing == ['github_token']


@pytest.mark.asyncio
async def test_ttl_sets_the_expiry(principal):
    user, org_a, _org_b = principal

    response = await _sign(user.id, org_a.id, ['PERSONAL_PAT'], ttl_seconds=3600)

    claims = _claims(response.secrets['PERSONAL_PAT'].headers['X-Access-Token'])
    assert claims['exp'] - claims['iat'] == 3600


@pytest.mark.parametrize(
    'body',
    [
        {'names': []},
        {'names': [f'NAME_{i}' for i in range(21)]},
        {'names': ['not-an-env-name']},
        {'names': ['A'], 'ttl_seconds': 3601},
        {'names': ['A'], 'ttl_seconds': 0},
    ],
)
def test_request_limits(body):
    with pytest.raises(ValidationError):
        SecretLookupsRequest.model_validate(body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'change', ['not-a-member', 'inactive', 'invited', 'disabled', 'deleting', 'unknown']
)
async def test_only_an_active_member_gets_references(
    principal, session_maker, create_org, change
):
    user, org_a, _org_b = principal
    org_id = org_a.id
    user_id = user.id
    with session_maker() as session:
        if change == 'not-a-member':
            org_id = create_org().id
        elif change in ('inactive', 'invited'):
            session.get(OrgMember, (org_a.id, user.id)).status = change
        elif change == 'disabled':
            session.get(User, user.id).is_disabled = True
        elif change == 'deleting':
            session.get(User, user.id).deletion_pending = True
        elif change == 'unknown':
            user_id = uuid4()
        session.commit()

    with pytest.raises(HTTPException) as exc:
        await _sign(user_id, org_id, ['PERSONAL_PAT'])

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_service_account_gets_references(principal, session_maker):
    user, org_a, _org_b = principal
    with session_maker() as session:
        session.get(User, user.id).account_type = 'service'
        session.commit()

    response = await _sign(user.id, org_a.id, ['SHARED_PAT', 'PERSONAL_PAT'])

    assert set(response.secrets) == {'SHARED_PAT', 'PERSONAL_PAT'}
    value = await _resolve(response.secrets['SHARED_PAT'].headers['X-Access-Token'])
    assert value.body.decode() == VALUES['SHARED_PAT']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('key', 'configured', 'status_code'),
    [(None, SERVICE_KEY, 401), ('wrong', SERVICE_KEY, 401), (SERVICE_KEY, '', 503)],
)
async def test_service_key_is_required(
    principal, monkeypatch, key, configured, status_code
):
    user, org_a, _org_b = principal
    monkeypatch.setattr(service_routes, 'AUTOMATIONS_SERVICE_KEY', configured)

    with pytest.raises(HTTPException) as exc:
        await create_secret_lookups(
            user_id=str(user.id),
            org_id=org_a.id,
            request=SecretLookupsRequest(names=['PERSONAL_PAT']),
            x_service_api_key=key,
            jwt_service=JWT,
        )

    assert exc.value.status_code == status_code


@pytest.mark.asyncio
async def test_no_lookup_url_is_unavailable(principal, monkeypatch):
    user, org_a, _org_b = principal
    monkeypatch.setattr(service_routes, 'sandbox_secret_lookup_url', lambda: None)

    with pytest.raises(HTTPException) as exc:
        await _sign(user.id, org_a.id, ['PERSONAL_PAT'])

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_route_is_served_with_the_service_key_header(principal):
    user, org_a, _org_b = principal
    app = FastAPI()
    app.include_router(service_router)
    app.dependency_overrides[jwt_dependency.dependency] = lambda: JWT
    path = f'/api/service/users/{user.id}/orgs/{org_a.id}/secret-lookups'

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://test'
    ) as client:
        ok = await client.post(
            path,
            json={'names': ['PERSONAL_PAT', 'MISSING_ONE']},
            headers={'X-Service-API-Key': SERVICE_KEY},
        )
        unauthorized = await client.post(path, json={'names': ['PERSONAL_PAT']})
        invalid = await client.post(
            path,
            json={'names': ['A'], 'ttl_seconds': 7200},
            headers={'X-Service-API-Key': SERVICE_KEY},
        )

    assert ok.status_code == 200
    assert ok.json()['missing'] == ['MISSING_ONE']
    assert set(ok.json()['secrets']) == {'PERSONAL_PAT'}
    _assert_no_values(ok.text)
    assert unauthorized.status_code == 401
    assert invalid.status_code == 422


@pytest.mark.parametrize(
    ('sandbox', 'web_url', 'sandbox_web_url', 'expected'),
    [
        (
            RemoteSandboxServiceInjector(api_url='https://rt', api_key='fake'),
            WEB_URL,
            None,
            LOOKUP_URL,
        ),
        (
            RemoteSandboxServiceInjector(api_url='https://rt', api_key='fake'),
            'http://localhost:3000',
            None,
            None,
        ),
        (
            RemoteSandboxServiceInjector(api_url='https://rt', api_key='fake'),
            None,
            None,
            None,
        ),
        (
            DockerSandboxServiceInjector(),
            None,
            None,
            'http://host.docker.internal:3000/api/v1/webhooks/secrets',
        ),
        (
            DockerSandboxServiceInjector(),
            WEB_URL,
            'http://sandbox-facing:3000',
            'http://sandbox-facing:3000/api/v1/webhooks/secrets',
        ),
    ],
)
def test_lookup_url_matches_the_conversation_one(
    monkeypatch, sandbox, web_url, sandbox_web_url, expected
):
    config = get_global_config()
    monkeypatch.setattr(config, 'sandbox', sandbox)
    monkeypatch.setattr(config, 'web_url', web_url)
    monkeypatch.setattr(config, 'sandbox_web_url', sandbox_web_url)

    assert sandbox_secret_lookup_url() == expected


# ---------------------------------------------------------------------------
# The lookup handler with org-bound references
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_reference_resolves_to_its_value(
    principal, github_connected, oauth_named_secret, caplog
):
    caplog.set_level(logging.INFO)
    user, org_a, _org_b = principal
    response = await _sign(
        user.id,
        org_a.id,
        ['PERSONAL_PAT', 'SHARED_PAT', 'GITHUB_TOKEN', 'PLUGINS_OAUTH'],
    )

    for name, ref in response.secrets.items():
        value = await _resolve(ref.headers['X-Access-Token'])
        assert value.body.decode() == VALUES[name], name
        assert value.media_type == 'text/plain'

    _assert_no_values(caplog.text)
    audit = [r for r in caplog.records if r.message == 'webhooks:secret_lookup']
    assert [(r.secret_kind, r.org_id, r.found) for r in audit] == [
        ('custom', str(org_a.id), True),
        ('custom', str(org_a.id), True),
        ('provider', str(org_a.id), True),
        ('oauth', str(org_a.id), True),
    ]


@pytest.mark.asyncio
async def test_org_a_reference_reads_org_a_while_current_org_is_b(
    principal, session_maker
):
    user, org_a, org_b = principal
    response = await _sign(user.id, org_a.id, ['PERSONAL_PAT', 'SHARED_PAT'])
    with session_maker() as session:
        assert session.get(User, user.id).current_org_id == org_b.id

    for name in ('PERSONAL_PAT', 'SHARED_PAT'):
        token = response.secrets[name].headers['X-Access-Token']
        assert (await _resolve(token)).body.decode() == VALUES[name]


@pytest.mark.asyncio
async def test_a_deleted_secret_is_not_found(principal):
    user, org_a, _org_b = principal
    response = await _sign(user.id, org_a.id, ['PERSONAL_PAT'])
    await SaasSecretsStore(
        str(user.id), JWT, effective_org_id=org_a.id
    ).delete_personal('PERSONAL_PAT')

    with pytest.raises(HTTPException) as exc:
        await _resolve(response.secrets['PERSONAL_PAT'].headers['X-Access-Token'])

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_principal_who_left_the_org_is_forbidden(principal, session_maker):
    user, org_a, _org_b = principal
    response = await _sign(user.id, org_a.id, ['SHARED_PAT'])
    with session_maker() as session:
        session.delete(session.get(OrgMember, (org_a.id, user.id)))
        session.commit()

    with pytest.raises(HTTPException) as exc:
        await _resolve(response.secrets['SHARED_PAT'].headers['X-Access-Token'])

    assert exc.value.status_code == 403
    _assert_no_values(str(exc.value.detail))


@pytest.mark.asyncio
async def test_a_disabled_principal_is_forbidden(principal, session_maker):
    user, org_a, _org_b = principal
    response = await _sign(user.id, org_a.id, ['SHARED_PAT'])
    with session_maker() as session:
        session.get(User, user.id).is_disabled = True
        session.commit()

    with pytest.raises(HTTPException) as exc:
        await _resolve(response.secrets['SHARED_PAT'].headers['X-Access-Token'])

    assert exc.value.status_code == 403


def _make_change(session_maker, user, org, change: str) -> None:
    with session_maker() as session:
        if change in ('inactive', 'invited'):
            session.get(OrgMember, (org.id, user.id)).status = change
        elif change == 'disabled':
            session.get(User, user.id).is_disabled = True
        elif change == 'deleting':
            session.get(User, user.id).deletion_pending = True
        session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize('super_admin_enabled', [False, True], ids=['sa-off', 'sa-on'])
@pytest.mark.parametrize('change', ['inactive', 'invited', 'disabled', 'deleting'])
async def test_redemption_requires_the_membership_the_route_required(
    principal, github_connected, session_maker, monkeypatch, change, super_admin_enabled
):
    # Signed while the membership was active; redeemed after it changed.
    monkeypatch.setattr(
        'server.auth.org_access.ENABLE_SUPER_ADMIN', super_admin_enabled
    )
    user, org_a, _org_b = principal
    response = await _sign(user.id, org_a.id, ['SHARED_PAT', 'GITHUB_TOKEN'])
    assert {ref.kind for ref in response.secrets.values()} == {'custom', 'provider'}
    for name, reference in response.secrets.items():
        resolved = await _resolve(reference.headers['X-Access-Token'])
        assert resolved.body.decode() == VALUES[name]

    _make_change(session_maker, user, org_a, change)

    for reference in response.secrets.values():
        with pytest.raises(HTTPException) as exc:
            await _resolve(reference.headers['X-Access-Token'])
        assert exc.value.status_code == 403
        _assert_no_values(str(exc.value.detail))


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['inactive', 'invited'])
async def test_an_instance_super_admin_gets_no_exception(
    principal, session_maker, async_session_maker, monkeypatch, change
):
    monkeypatch.setattr('server.auth.org_access.ENABLE_SUPER_ADMIN', True)
    monkeypatch.setattr('storage.role_store.a_session_maker', async_session_maker)
    # This directory's conftest stubs the super-role lookup out; use the real one.
    monkeypatch.setattr(
        'server.auth.authorization.get_user_super_role', get_user_super_role
    )
    user, org_a, _org_b = principal
    with session_maker() as session:
        admin = session.query(Role).filter(Role.name == 'admin').one_or_none()
        if admin is None:
            admin = Role(name='admin', rank=20)
            session.add(admin)
            session.flush()
        session.get(User, user.id).role_id = admin.id
        session.commit()
    assert await is_instance_super_admin(str(user.id))
    response = await _sign(user.id, org_a.id, ['SHARED_PAT'])
    token = response.secrets['SHARED_PAT'].headers['X-Access-Token']
    assert (await _resolve(token)).body.decode() == VALUES['SHARED_PAT']

    _make_change(session_maker, user, org_a, change)

    # The product gate still lets an instance super admin in; redemption does not.
    await assert_org_usable_for_product(org_a.id, user_id=str(user.id))
    with pytest.raises(HTTPException) as exc:
        await _resolve(token)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_an_expired_reference_is_unauthorized(principal):
    user, org_a, _org_b = principal
    expired = JWT.create_jws_token(
        payload={
            'user_id': str(user.id),
            'org_id': str(org_a.id),
            'secret_name': 'PERSONAL_PAT',
        },
        expires_in=timedelta(seconds=-1),
        audience=JwtAudience.SECRET_LOOKUP,
    )

    with pytest.raises(HTTPException) as exc:
        await _resolve(expired)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_reference_expires_after_its_ttl(principal):
    user, org_a, _org_b = principal
    response = await _sign(user.id, org_a.id, ['PERSONAL_PAT'], ttl_seconds=1)
    token = response.secrets['PERSONAL_PAT'].headers['X-Access-Token']
    assert (await _resolve(token)).body.decode() == VALUES['PERSONAL_PAT']

    await asyncio.sleep(2.1)

    with pytest.raises(HTTPException) as exc:
        await _resolve(token)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'claims',
    [
        # A custom secret is only ever named together with an org.
        {'secret_name': 'PERSONAL_PAT'},
        # Exactly one target.
        {'org_id': None, 'secret_name': 'PERSONAL_PAT', 'provider_type': 'github'},
        {'org_id': None},
        {'org_id': 'not-a-uuid', 'secret_name': 'PERSONAL_PAT'},
    ],
)
async def test_malformed_references_are_unauthorized(principal, claims):
    user, org_a, _org_b = principal
    payload = {'user_id': str(user.id), **claims}
    if payload.get('org_id', '') is None:
        payload['org_id'] = str(org_a.id)
    token = JWT.create_jws_token(payload=payload, audience=JwtAudience.SECRET_LOOKUP)

    with pytest.raises(HTTPException) as exc:
        await _resolve(token)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_the_system_api_key_is_never_resolved(principal):
    user, org_a, _org_b = principal
    token = JWT.create_jws_token(
        payload={
            'user_id': str(user.id),
            'org_id': str(org_a.id),
            'secret_name': 'OPENHANDS_API_KEY',
        },
        audience=JwtAudience.SECRET_LOOKUP,
    )

    with pytest.raises(HTTPException) as exc:
        await _resolve(token)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_disconnected_provider_is_not_found(principal, monkeypatch):
    user, org_a, _org_b = principal
    token = JWT.create_jws_token(
        payload={
            'user_id': str(user.id),
            'org_id': str(org_a.id),
            'provider_type': 'github',
        },
        audience=JwtAudience.SECRET_LOOKUP,
    )
    monkeypatch.setattr(
        SaasUserAuth,
        'get_provider_tokens',
        AsyncMock(return_value=MappingProxyType({})),
    )

    with pytest.raises(HTTPException) as exc:
        await _resolve(token)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_reference_from_another_audience_is_unauthorized(principal):
    user, org_a, _org_b = principal
    session_cookie = JWT.create_jws_token(
        payload={
            'user_id': str(user.id),
            'org_id': str(org_a.id),
            'secret_name': 'PERSONAL_PAT',
        },
        audience=JwtAudience.SESSION,
    )

    with pytest.raises(HTTPException) as exc:
        await _resolve(session_cookie)

    assert exc.value.status_code == 401


def _signed(claims: dict) -> str:
    """A token signed with the service's key, carrying exactly ``claims``."""
    key_id = JWT.default_key_id
    key = JWT.get_key(key_id).key.get_secret_value()
    return pyjwt.encode(claims, key, algorithm='HS256', headers={'kid': key_id})


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['secret_name', 'provider_type'])
@pytest.mark.parametrize(
    'variant', ['no-audience', 'other-audience', 'audience-list', 'no-expiry']
)
async def test_an_org_reference_must_name_its_audience_and_expire(
    principal, github_connected, target, variant
):
    user, org_a, _org_b = principal
    now = int(time.time())
    payload = {
        'user_id': str(user.id),
        'org_id': str(org_a.id),
        target: 'PERSONAL_PAT' if target == 'secret_name' else 'github',
    }
    if variant == 'no-audience':
        # Signed the way tokens were before audiences; only conversation
        # references may still arrive like that.
        token = mint_unscoped_jws_token(JWT, payload)
    elif variant == 'other-audience':
        token = JWT.create_jws_token(
            payload=payload, audience=JwtAudience.JIRA_DC_SECRET
        )
    elif variant == 'audience-list':
        token = _signed(
            {
                **payload,
                'aud': [JwtAudience.SECRET_LOOKUP.value, JwtAudience.SESSION.value],
                'iat': now,
                'exp': now + 600,
            }
        )
    else:
        token = _signed({**payload, 'aud': JwtAudience.SECRET_LOOKUP.value, 'iat': now})

    with pytest.raises(HTTPException) as exc:
        await _resolve(token)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize('minted', ['with-audience', 'before-audiences'])
async def test_conversation_references_resolve_as_before(
    principal, github_connected, minted
):
    # Conversations sign references without an org: they keep today's rules,
    # including the temporary acceptance of tokens minted before audiences.
    user, _org_a, _org_b = principal
    payload = {'user_id': str(user.id), 'provider_type': 'github'}
    if minted == 'with-audience':
        token = JWT.create_jws_token(
            payload=payload, audience=JwtAudience.SECRET_LOOKUP
        )
    else:
        token = mint_unscoped_jws_token(JWT, payload)

    resolved = await _resolve(token)

    assert resolved.body.decode() == VALUES['GITHUB_TOKEN']


# ---------------------------------------------------------------------------
# End to end: an SDK LookupSecret fetches a plugin from a private repository
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture
def lookup_server() -> Iterator[str]:
    """The webhook router served over HTTP, as a sandbox reaches it."""
    app = FastAPI()
    app.include_router(webhook_router.router, prefix='/api/v1')
    app.dependency_overrides[webhook_router.jwt_dependency.dependency] = lambda: JWT
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning')
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.asyncio
async def test_sdk_preflight_clones_a_private_repository_with_a_reference(
    principal, lookup_server, tmp_path, caplog
):
    from openhands.sdk.plugin.strict_fetch import PluginSourceError, preflight_plugin
    from openhands.sdk.secret import LookupSecret

    caplog.set_level(logging.INFO)
    user, org_a, _org_b = principal
    token = f'fake-git-token-{pysecrets.token_hex(8)}'
    VALUES['PLUGIN_GIT_TOKEN'] = token
    try:
        await SaasSecretsStore(
            str(user.id), JWT, effective_org_id=org_a.id
        ).upsert_personal('PLUGIN_GIT_TOKEN', token)
        sha = make_bare_repo(
            tmp_path,
            'plugin',
            {
                '.plugin/plugin.json': json.dumps(
                    {'name': 'private-plugin', 'version': '0.1.0'}
                ),
                'run.sh': 'echo private plugin\n',
            },
        )
        response = await _sign(user.id, org_a.id, ['PLUGIN_GIT_TOKEN'])
        reference = response.secrets['PLUGIN_GIT_TOKEN']
        lookup = LookupSecret(
            url=lookup_server + response.lookup_path, headers=reference.headers
        )

        with PrivateGitServer(tmp_path, token) as git_server:
            host = git_server.url.removeprefix('http://')
            source = f'http://x-access-token:${{PLUGIN_GIT_TOKEN}}@{host}/plugin.git'

            def preflight(secrets):
                with preflight_plugin(
                    source, secrets=secrets, allow_local=True
                ) as checked:
                    return checked.resolved_ref, checked.source

            resolved_ref, echoed = await asyncio.to_thread(
                preflight, {'PLUGIN_GIT_TOKEN': lookup}
            )
            assert resolved_ref == sha
            assert echoed == source

            with pytest.raises(PluginSourceError) as missing:
                await asyncio.to_thread(preflight, {})
            assert missing.value.code == 'missing_secret'

            await SaasSecretsStore(
                str(user.id), JWT, effective_org_id=org_a.id
            ).upsert_personal('PLUGIN_GIT_TOKEN', 'fake-rotated-wrong-token')
            with pytest.raises(PluginSourceError) as rejected:
                await asyncio.to_thread(preflight, {'PLUGIN_GIT_TOKEN': lookup})
            assert rejected.value.code == 'auth_failed'
            assert 'fake-rotated-wrong-token' not in str(rejected.value)
            assert token not in str(rejected.value)

        assert (('/plugin.git/info/refs', True)) in git_server.requests
        _assert_no_values(caplog.text)
        assert 'fake-rotated-wrong-token' not in caplog.text
    finally:
        VALUES.pop('PLUGIN_GIT_TOKEN', None)


def test_reference_payload_names_one_org_and_one_secret():
    """The JWS carries no value: only the ids that select one secret."""
    target = secret_lookup_service.SecretLookupTarget(
        kind='custom', claims={'secret_name': 'PERSONAL_PAT'}
    )
    org_id = UUID('00000000-0000-4000-8000-000000000001')

    token = secret_lookup_service.sign_secret_lookup(
        JWT, user_id='user-1', org_id=org_id, target=target, ttl_seconds=900
    )

    claims = pyjwt.decode(token, options={'verify_signature': False})
    assert set(claims) == {'user_id', 'org_id', 'secret_name', 'aud', 'iat', 'exp'}
    assert claims['org_id'] == str(org_id)
