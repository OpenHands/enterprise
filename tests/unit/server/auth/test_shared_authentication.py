"""Security boundaries shared by local browser sessions and Enterprise API keys."""

import ast
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.auth import mode
from server.auth.authentication import AuthenticationService, active_account
from server.auth.contracts import (
    InvalidCredentials,
    RequestCredentials,
)
from server.auth.identities import IdentityRepository
from server.auth.keycloak import manager
from server.auth.local.accounts import create_local_account
from server.auth.local.sessions import LocalBrowserSessionBackend
from server.middleware import SetAuthCookieMiddleware
from server.routes.auth_capabilities import router as capabilities_router
from storage.api_key_store import ApiKeyStore, ApiKeyValidationResult
from storage.role import Role
from storage.user import User
from storage.user_store import UserStore
from tests.unit.auth_schema import create_auth_schema


@pytest.fixture
async def auth_database(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(create_auth_schema)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.LOCAL)
    monkeypatch.setattr('server.auth.identities.a_session_maker', factory)
    monkeypatch.setattr('storage.user_authorization_store.a_session_maker', factory)
    async with factory() as session, session.begin():
        session.add_all(
            [
                Role(id=1, name='owner', rank=1),
                Role(id=2, name='admin', rank=2),
                Role(id=3, name='member', rank=3),
            ]
        )
    async with factory() as session, session.begin():
        user = await create_local_account(
            session,
            'person+tag@example.com',
            SecretStr('An adequately long test password'),
        )
    backend = LocalBrowserSessionBackend(factory)
    monkeypatch.setattr(
        'server.auth.local.sessions.LocalBrowserSessionBackend', lambda: backend
    )

    async def get_user(user_id):
        async with factory() as session:
            return await session.get(User, UUID(str(user_id)))

    monkeypatch.setattr(UserStore, 'get_user_by_id', get_user)
    monkeypatch.setattr(
        manager,
        'KeycloakOpenID',
        lambda *a, **k: pytest.fail('Local authentication constructed Keycloak'),
    )
    monkeypatch.setattr(
        manager,
        'KeycloakAdmin',
        lambda *a, **k: pytest.fail('Local authentication constructed Keycloak'),
    )
    yield factory, user, backend
    await engine.dispose()


def request(headers=None, cookies=None, path='/api/v1/users/me', method='GET'):
    result = Request(
        {
            'type': 'http',
            'method': method,
            'path': path,
            'scheme': 'https',
            'server': ('app.example.com', 443),
            'headers': [
                (name.lower().encode(), value.encode())
                for name, value in (headers or {}).items()
            ],
            'query_string': b'',
        }
    )
    result._cookies = cookies or {}
    return result


async def test_local_session_resolves_canonical_account_without_keycloak(auth_database):
    _, user, backend = auth_database
    issued = await backend.issue(user.id)
    auth = await AuthenticationService().authenticate_request(
        request(cookies={'oh_session': issued.token.get_secret_value()})
    )
    assert auth.principal.user_id == user.id
    assert auth.principal.authentication_method == 'password'
    assert await auth.get_access_token() is None
    await auth.refresh()
    assert await auth.get_effective_org_id() == user.current_org_id


async def test_disabled_account_rejected_even_when_cookie_was_valid(auth_database):
    factory, user, backend = auth_database
    issued = await backend.issue(user.id)
    async with factory() as session, session.begin():
        (await session.get(User, user.id)).is_disabled = True
    with pytest.raises(InvalidCredentials):
        await AuthenticationService().authenticate(
            RequestCredentials(cookies={'oh_session': issued.token.get_secret_value()})
        )


async def test_keycloak_existing_account_explicitly_backfills_missing_profile(
    auth_database, monkeypatch
):
    factory, user, _ = auth_database
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    async with factory() as session, session.begin():
        (await session.get(User, user.id)).email_verified = None
    hydrate = AsyncMock(return_value=user)
    monkeypatch.setattr(
        'server.auth.user_management.EnterpriseUserManagementService.ensure_authenticated_account',
        hydrate,
    )
    assert (await active_account(user.id)).id == user.id
    hydrate.assert_awaited_once_with(user.id)


async def test_trusted_legacy_background_entry_hydrates_without_offline_session(
    auth_database, monkeypatch
):
    from server.auth.saas_user_auth import SaasUserAuth

    _, user, _ = auth_database
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    hydrate = AsyncMock(return_value=user)
    monkeypatch.setattr(
        'server.auth.user_management.EnterpriseUserManagementService.ensure_authenticated_account',
        hydrate,
    )
    monkeypatch.setattr(
        'server.auth.keycloak.token_manager.TokenManager.load_offline_token',
        AsyncMock(side_effect=AssertionError('Background read an offline session')),
    )
    context = await SaasUserAuth.for_background(str(user.id))
    hydrate.assert_awaited_once_with(user.id)
    assert context.principal.user_id == user.id
    assert context.principal.authentication_method == 'background'
    assert context.refresh_token is None
    assert await context.get_access_token() is None


async def test_exact_legacy_broker_actor_hydrates_the_proven_subject(
    auth_database, monkeypatch
):
    from unittest.mock import MagicMock

    from openhands.app_server.integrations.service_types import ProviderType
    from server.auth.keycloak.providers import resolve_broker_actor

    _, user, _ = auth_database
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    identity = {
        'id': str(user.id),
        'email': user.email,
        'emailVerified': True,
        'enabled': True,
        'attributes': {'github_id': ['actor-1']},
    }
    monkeypatch.setattr(
        manager,
        'get_keycloak_admin',
        lambda: MagicMock(a_get_users=AsyncMock(return_value=[identity])),
    )
    hydrate = AsyncMock(return_value=user)
    monkeypatch.setattr(
        'server.auth.user_management.EnterpriseUserManagementService.ensure_authenticated_account',
        hydrate,
    )
    assert await resolve_broker_actor(ProviderType.GITHUB, 'actor-1') == user.id
    hydrate.assert_awaited_once_with(user.id, {**identity, 'email_verified': True})
    hydrate.reset_mock()
    assert await resolve_broker_actor(ProviderType.GITHUB, 'different-actor') is None
    hydrate.assert_not_awaited()


async def test_proven_legacy_broker_token_hydrates_before_credential_lookup(
    auth_database, monkeypatch
):
    from server.auth.keycloak.providers import user_from_broker_token
    from server.auth.keycloak.token_manager import KeycloakUserInfo, TokenManager

    _, user, _ = auth_database
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    info = KeycloakUserInfo(sub=str(user.id), email=user.email, email_verified=True)
    monkeypatch.setattr(TokenManager, 'get_user_info', AsyncMock(return_value=info))
    hydrate = AsyncMock(return_value=user)
    monkeypatch.setattr(
        'server.auth.user_management.EnterpriseUserManagementService.ensure_authenticated_account',
        hydrate,
    )
    assert await user_from_broker_token(SecretStr('access')) == str(user.id)
    hydrate.assert_awaited_once_with(user.id, info.model_dump(exclude_none=True))
    user.is_disabled = True
    with pytest.raises(InvalidCredentials):
        await user_from_broker_token(SecretStr('access'))


def test_enterprise_configuration_allows_disabled_analytics(monkeypatch):
    from server.config import SaaSServerConfig

    monkeypatch.setattr('server.config.GITHUB_APP_CLIENT_ID', '')
    monkeypatch.setattr('server.config.GITHUB_APP_PRIVATE_KEY', '')
    config = SaaSServerConfig()
    config.config_cls = 'server.config.SaaSServerConfig'
    config.posthog_client_key = ''
    config.verify_config()


@pytest.mark.parametrize(
    'header', ['Authorization', 'X-Session-API-Key', 'X-Access-Token']
)
async def test_api_key_headers_win_over_other_browser_principal(
    auth_database, monkeypatch, header
):
    _, user, backend = auth_database
    issued = await backend.issue(user.id)
    validation = ApiKeyValidationResult(
        user_id=str(user.id), key_id=12, key_name='SDK', org_id=user.current_org_id
    )
    validator = AsyncMock(return_value=validation)
    monkeypatch.setattr(ApiKeyStore, 'validate_api_key', validator)
    req = request(
        headers={
            header: 'Bearer api-secret' if header == 'Authorization' else 'api-secret'
        },
        cookies={'oh_session': issued.token.get_secret_value()},
    )
    auth = await AuthenticationService().authenticate_request(req)
    assert auth.principal.authentication_method == 'api_key'
    assert auth.api_key_id == 12
    assert req.state.authentication_via_cookie is False
    validator.assert_awaited_once_with('api-secret')


async def test_invalid_header_cookie_fallback_keeps_csrf_requirement(
    auth_database, monkeypatch
):
    _, user, backend = auth_database
    issued = await backend.issue(user.id)
    monkeypatch.setattr(ApiKeyStore, 'validate_api_key', AsyncMock(return_value=None))
    req = request(
        headers={'Authorization': 'Bearer invalid'},
        cookies={'oh_session': issued.token.get_secret_value()},
        method='POST',
    )
    auth = await AuthenticationService().authenticate_request(req)
    assert req.state.authentication_via_cookie is True
    req.state.user_auth = auth
    downstream = AsyncMock()
    response = await SetAuthCookieMiddleware()(req, downstream)
    assert response.status_code == 403
    downstream.assert_not_awaited()


async def test_api_key_cookie_requires_csrf(auth_database, monkeypatch):
    _, user, _ = auth_database
    monkeypatch.setattr(
        ApiKeyStore,
        'validate_api_key',
        AsyncMock(
            return_value=ApiKeyValidationResult(
                user_id=str(user.id), key_id=1, key_name='cookie', org_id=None
            )
        ),
    )
    req = request(cookies={'api_key': 'secret'}, method='POST')
    req.state.user_auth = await AuthenticationService().authenticate_request(req)
    response = await SetAuthCookieMiddleware()(req, AsyncMock())
    assert response.status_code == 403


async def test_non_uuid_api_key_owner_is_rejected_without_cookie_fallback(
    auth_database, monkeypatch
):
    _, user, backend = auth_database
    issued = await backend.issue(user.id)
    monkeypatch.setattr(
        ApiKeyStore,
        'validate_api_key',
        AsyncMock(
            return_value=ApiKeyValidationResult(
                user_id='not-a-uuid', key_id=1, key_name='broken', org_id=None
            )
        ),
    )
    with pytest.raises(InvalidCredentials):
        await AuthenticationService().authenticate(
            RequestCredentials(
                bearer_token=SecretStr('valid-key'),
                cookies={'oh_session': issued.token.get_secret_value()},
            )
        )


async def test_bound_key_rejects_conflicting_org_header(auth_database, monkeypatch):
    _, user, _ = auth_database
    monkeypatch.setattr(
        ApiKeyStore,
        'validate_api_key',
        AsyncMock(
            return_value=ApiKeyValidationResult(
                user_id=str(user.id),
                key_id=1,
                key_name='org key',
                org_id=user.current_org_id,
            )
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await AuthenticationService().authenticate_request(
            request(headers={'Authorization': 'Bearer key', 'X-Org-Id': str(uuid4())})
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    'path', ['/api/authenticate', '/api/keys', '/oauth/device/verify-authenticated']
)
async def test_restricted_password_precedes_terms_and_authenticate(auth_database, path):
    _, user, backend = auth_database
    issued = await backend.issue(user.id, restricted=True)
    req = request(
        cookies={'oh_session': issued.token.get_secret_value()},
        path=path,
        method='POST',
    )
    req.state.user_auth = await AuthenticationService().authenticate_request(req)
    downstream = AsyncMock()
    response = await SetAuthCookieMiddleware()(req, downstream)
    assert response.status_code == 403
    assert b'password_change_required' in response.body
    downstream.assert_not_awaited()


async def test_identity_link_is_idempotent_and_cannot_be_reassigned(auth_database):
    factory, user, _ = auth_database
    repo = IdentityRepository()
    await repo.link(
        user.id, 'keycloak', 'https://id.example/realms/enterprise', str(user.id)
    )
    await repo.link(
        user.id, 'keycloak', 'https://id.example/realms/enterprise', str(user.id)
    )
    async with factory() as session, session.begin():
        other = await create_local_account(
            session, 'other@example.com', SecretStr('Another adequately long password')
        )
    with pytest.raises(InvalidCredentials):
        await repo.link(
            other.id, 'keycloak', 'https://id.example/realms/enterprise', str(user.id)
        )
    assert (
        await repo.resolve(
            'keycloak', 'https://id.example/realms/enterprise', str(user.id)
        )
        == user.id
    )


async def test_capabilities_local_never_construct_keycloak(auth_database, monkeypatch):
    monkeypatch.setattr(
        'server.routes.auth_capabilities.csrf_seed',
        lambda request, response: 'test-seed',
    )
    app = FastAPI()
    app.include_router(capabilities_router)
    async with AsyncClient(
        transport=ASGITransport(app), base_url='https://app.example.com'
    ) as client:
        response = await client.get('/api/auth/capabilities')
        assert response.json() == {
            'mode': 'local',
            'password_login': True,
            'login_providers': [],
            'registration': 'admin_or_invitation',
            'email_recovery': False,
            'repository_connections': {'manual_tokens': True, 'broker': False},
        }
        assert (
            await client.get('/api/auth/authorize?provider=github')
        ).status_code == 404
        assert (await client.get('/api/auth/providers/github/link')).status_code == 404


def test_keycloak_clients_reject_local_mode_before_construction(monkeypatch):
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.LOCAL)
    with pytest.raises(InvalidCredentials):
        manager.get_keycloak_openid()
    with pytest.raises(InvalidCredentials):
        manager.get_keycloak_admin()


def test_auth_adapter_import_boundaries():
    root = Path(__file__).resolve().parents[4]
    for base in ('server', 'storage', 'integrations', 'sync', 'analytics', 'utils'):
        for path in (root / base).rglob('*.py'):
            if '/server/auth/keycloak/' in str(path):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.module or '').startswith('keycloak'), path
                elif isinstance(node, ast.Import):
                    assert all(
                        not name.name.startswith('keycloak') for name in node.names
                    ), path


@pytest.mark.parametrize(
    'credential', ['local_cookie', 'api_cookie', 'invalid_header_cookie']
)
async def test_sdk_exposure_rejects_browser_credentials_even_with_owned_sandbox(
    auth_database, monkeypatch, credential
):
    from openhands.app_server.user.auth_user_context import AuthUserContext
    from server.routes.users_v1 import get_current_user_saas

    _, user, backend = auth_database
    issued = await backend.issue(user.id)
    cookies = {'oh_session': issued.token.get_secret_value()}
    headers = {}
    if credential == 'api_cookie':
        cookies = {'api_key': 'valid-api-key'}
        monkeypatch.setattr(
            ApiKeyStore,
            'validate_api_key',
            AsyncMock(
                return_value=ApiKeyValidationResult(
                    user_id=str(user.id), key_id=1, key_name='cookie', org_id=None
                )
            ),
        )
    elif credential == 'invalid_header_cookie':
        headers = {'Authorization': 'Bearer invalid'}
        monkeypatch.setattr(
            ApiKeyStore, 'validate_api_key', AsyncMock(return_value=None)
        )
    auth = await AuthenticationService().authenticate_request(
        request(headers=headers, cookies=cookies)
    )
    validate_sandbox = AsyncMock()
    monkeypatch.setattr(
        'server.routes.users_v1.validate_session_key_ownership', validate_sandbox
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_user_saas(
            AuthUserContext(auth),
            expose_secrets=True,
            x_session_api_key='owned-running-sandbox',
        )
    assert exc.value.status_code == 403
    validate_sandbox.assert_not_awaited()


@pytest.mark.parametrize(
    'sandbox_state, matching_owner, expected',
    [('RUNNING', True, 200), ('RUNNING', False, 403), ('PAUSED', True, 401)],
)
async def test_sdk_exposure_requires_real_bearer_and_owned_running_sandbox(
    auth_database, monkeypatch, sandbox_state, matching_owner, expected
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from openhands.app_server.sandbox.sandbox_models import SandboxStatus
    from openhands.app_server.user.auth_user_context import AuthUserContext
    from openhands.app_server.user.user_models import UserInfo
    from server.routes.users_v1 import get_current_user_saas

    _, user, _ = auth_database
    monkeypatch.setattr(
        ApiKeyStore,
        'validate_api_key',
        AsyncMock(
            return_value=ApiKeyValidationResult(
                user_id=str(user.id), key_id=1, key_name='SDK', org_id=None
            )
        ),
    )
    auth = await AuthenticationService().authenticate_request(
        request(headers={'Authorization': 'Bearer valid-key'})
    )
    context = AuthUserContext(auth, _user_info=UserInfo(id=str(user.id)))
    sandbox = SimpleNamespace(
        id='sandbox-id',
        status=SandboxStatus(sandbox_state),
        created_by_user_id=str(user.id) if matching_owner else str(uuid4()),
    )

    @asynccontextmanager
    async def service(_state):
        yield SimpleNamespace(
            get_sandbox_by_session_api_key=AsyncMock(return_value=sandbox)
        )

    monkeypatch.setattr(
        'openhands.app_server.sandbox.session_auth.get_sandbox_service', service
    )
    monkeypatch.setattr(
        'server.routes.users_v1._get_org_info_from_context',
        AsyncMock(return_value=None),
    )
    if expected == 200:
        assert (
            await get_current_user_saas(context, True, 'session-key')
        ).status_code == 200
    else:
        with pytest.raises(HTTPException) as exc:
            await get_current_user_saas(context, True, 'session-key')
        assert exc.value.status_code == expected
