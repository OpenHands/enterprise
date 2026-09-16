"""Native runtime integration against migrated PostgreSQL and real credentials."""

import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import update

from openhands.app_server.middleware import LocalhostCORSMiddleware
from openhands.app_server.user.auth_user_context import AuthUserContext
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.user.user_models import UserInfo
from openhands.app_server.user_auth import user_auth as user_auth_module
from server import config, middleware
from server.auth import (
    account_lookup,
    auth_config,
    browser_policy,
    composition,
    keycloak_manager,
    openhands_request_auth,
    saas_user_auth,
)
from server.auth.auth_error import BearerTokenError
from server.auth.bootstrap import initialize_auth_installation
from server.auth.browser_policy import OpenHandsBrowserPolicy
from server.auth.native_types import SessionFactory
from server.auth.saas_user_auth import SaasUserAuth
from server.routes import auth, native_auth
from server.services import native_auth_service, native_integration_auth
from storage import api_key_store, role_store, user_store
from storage.api_key_store import ApiKeyStore
from storage.native_auth import AuthAccount
from storage.role import Role
from storage.user import User
from tests.unit.server.auth.native_test_types import (
    NativeRuntime,
    get_csrf_token,
)

ORIGIN = 'https://native.example.com'
FOREIGN_ORIGIN = 'https://untrusted.example.com'
PASSWORD = 'the telescope finds violet moons'


@pytest.fixture
async def native_runtime(
    monkeypatch: pytest.MonkeyPatch, async_session_maker: SessionFactory
) -> AsyncIterator[NativeRuntime]:
    for module in (
        auth_config,
        config,
        keycloak_manager,
        auth,
    ):
        monkeypatch.setattr(module, 'ENABLE_KEYCLOAK', False)
    composition.get_auth_services.cache_clear()
    monkeypatch.setattr(auth_config, 'AUTH_MODE', 'native')
    monkeypatch.setattr(config, 'AUTH_MODE', 'native')
    monkeypatch.setenv('OH_WEB_URL', ORIGIN)
    monkeypatch.setenv('SUPERADMIN_EMAIL', 'native@example.com')
    monkeypatch.setenv('SUPERADMIN_PASSWORD', PASSWORD)
    monkeypatch.delenv('PERMITTED_CORS_ORIGINS', raising=False)
    for name in list(os.environ):
        if name.startswith('OH_PERMITTED_CORS_ORIGINS_'):
            monkeypatch.delenv(name)
    auth_config.get_native_auth_settings.cache_clear()
    monkeypatch.setattr(saas_user_auth, 'rate_limiter', None)
    monkeypatch.setattr(
        user_auth_module.server_config,
        'user_auth_class',
        'server.auth.saas_user_auth.SaasUserAuth',
    )
    for module in (user_store, api_key_store, role_store, auth, account_lookup):
        monkeypatch.setattr(module, 'a_session_maker', async_session_maker)
    service = native_auth_service.NativeAuthService(async_session_maker)
    monkeypatch.setattr(native_auth_service, 'get_native_auth_service', lambda: service)
    monkeypatch.setattr(native_auth, 'get_native_auth_service', lambda: service)
    monkeypatch.setattr(
        openhands_request_auth, 'get_native_auth_service', lambda: service
    )
    # Jira route collection may import this function before the fixture runs.
    monkeypatch.setattr(
        native_integration_auth, 'get_native_auth_service', lambda: service
    )
    # The migrated template intentionally clears seeded role/config rows.
    async with async_session_maker() as session, session.begin():
        session.add_all(
            [
                Role(name='owner', rank=0),
                Role(name='admin', rank=1),
                Role(name='member', rank=2),
            ]
        )

    with (
        patch.object(
            keycloak_manager,
            'KeycloakOpenID',
            side_effect=AssertionError('Keycloak constructor called'),
        ),
        patch.object(
            keycloak_manager,
            'KeycloakAdmin',
            side_effect=AssertionError('Keycloak constructor called'),
        ),
    ):
        await initialize_auth_installation(session_factory=async_session_maker)
        login = await service.login(
            'native@example.com', PASSWORD, client_ip='127.0.0.1'
        )
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(User)
                .where(User.id == login.principal.account_id)
                .values(accepted_tos=datetime.now())
            )
        api_key = await ApiKeyStore().create_api_key(str(login.principal.account_id))
        yield service, login, api_key
    auth_config.get_native_auth_settings.cache_clear()
    composition.get_auth_services.cache_clear()


def native_app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth.api_router)
    app.include_router(native_auth.native_auth_router)

    @app.post('/api/v1/native-test')
    async def mutation(request: Request) -> dict[str, str | None]:
        return {
            'origin': request.headers.get('origin'),
            'transport': request.state.user_auth.credential_transport,
        }

    app.add_middleware(LocalhostCORSMiddleware)
    app.middleware('http')(middleware.SetAuthCookieMiddleware())
    app.add_middleware(middleware.ApiKeyAwareCORSMiddleware, allow_origins=[ORIGIN])
    return app


@pytest.mark.asyncio
async def test_native_valid_bearer_retains_origin_and_strips_inner_credentials(
    native_runtime: NativeRuntime,
) -> None:
    _, _, api_key = native_runtime
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=native_app()), base_url=ORIGIN
    ) as client:
        response = await client.post(
            '/api/v1/native-test',
            headers={
                'Origin': FOREIGN_ORIGIN,
                'Authorization': f'Bearer {api_key}',
            },
        )
    assert response.status_code == 200
    assert response.json() == {'origin': FOREIGN_ORIGIN, 'transport': 'bearer'}
    assert response.headers['access-control-allow-origin'] == '*'
    assert 'access-control-allow-credentials' not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'headers',
    [
        {'Authorization': 'Bearer invalid'},
        {'Authorization': 'Basic invalid'},
        {'X-Session-API-Key': ''},
        {'X-Access-Token': ''},
    ],
)
async def test_native_explicit_invalid_header_never_falls_back_to_cookie(
    native_runtime: NativeRuntime, headers: dict[str, str]
) -> None:
    _, login, api_key = native_runtime
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=native_app()),
        base_url=ORIGIN,
        cookies={'openhands_session': login.token, 'api_key': api_key},
    ) as client:
        response = await client.post(
            '/api/v1/native-test',
            headers={
                **headers,
                'Origin': ORIGIN,
                'X-CSRF-Token': await get_csrf_token(client),
            },
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_native_api_key_cookie_needs_csrf_and_trusted_origin(
    native_runtime: NativeRuntime,
) -> None:
    _, _, api_key = native_runtime
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=native_app()),
        base_url=ORIGIN,
        cookies={'api_key': api_key},
    ) as client:
        csrf = await get_csrf_token(client)
        missing = await client.post('/api/v1/native-test', headers={'Origin': ORIGIN})
        foreign = await client.post(
            '/api/v1/native-test',
            headers={'Origin': FOREIGN_ORIGIN, 'X-CSRF-Token': csrf},
        )
        valid = await client.post(
            '/api/v1/native-test', headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf}
        )
    assert missing.status_code == 403
    assert foreign.status_code == 403
    assert foreign.headers.get('access-control-allow-origin') is None
    assert valid.status_code == 200
    assert valid.json()['transport'] == 'api_key_cookie'


@pytest.mark.asyncio
async def test_native_logout_revokes_browser_cookie_even_with_bearer(
    native_runtime: NativeRuntime,
) -> None:
    service, login, api_key = native_runtime
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=native_app()),
        base_url=ORIGIN,
        cookies={'openhands_session': login.token},
    ) as client:
        missing = await client.post(
            '/api/logout',
            headers={'Authorization': f'Bearer {api_key}', 'Origin': ORIGIN},
        )
        assert missing.status_code == 403
        response = await client.post(
            '/api/logout',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Origin': ORIGIN,
                'X-CSRF-Token': await get_csrf_token(client),
            },
        )
    assert response.status_code == 200
    assert 'Max-Age=0' in response.headers['set-cookie']
    assert await service.authenticate_session(login.token) is None
    assert await ApiKeyStore().validate_api_key(api_key) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'state', ['deleted', 'profile_absent_blocked', 'reonboardable']
)
async def test_native_ineligible_account_blocks_api_and_background_context(
    native_runtime: NativeRuntime, async_session_maker: SessionFactory, state: str
) -> None:
    _, login, api_key = native_runtime
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(AuthAccount)
            .where(AuthAccount.id == login.principal.account_id)
            .values(state=state)
        )
    request = Request(
        {'type': 'http', 'headers': [(b'authorization', f'Bearer {api_key}'.encode())]}
    )
    with pytest.raises(BearerTokenError):
        await SaasUserAuth.get_instance(request)
    with pytest.raises(saas_user_auth.NoCredentialsError):
        await SaasUserAuth.get_for_user(str(login.principal.account_id))


@pytest.mark.asyncio
async def test_native_unverified_email_and_global_permissions(
    native_runtime: NativeRuntime,
) -> None:
    _, login, _ = native_runtime
    from server.routes.users_v1 import get_current_user_saas

    request = Request(
        {
            'type': 'http',
            'headers': [(b'cookie', f'openhands_session={login.token}'.encode())],
        }
    )
    user_auth = await SaasUserAuth.get_instance(request)
    assert user_auth.email_verified is False
    assert await user_auth.get_access_token() is None
    user_auth._org_info_loaded = True
    context = AuthUserContext(
        user_auth=user_auth, _user_info=UserInfo(id=str(login.principal.account_id))
    )
    response = await get_current_user_saas(context, expose_secrets=False)
    body = json.loads(response.body)
    assert 'manage_users' in body['global_permissions']
    assert body['permissions'] is None


async def test_global_permissions_use_the_user_context_identity_contract(
    native_runtime: NativeRuntime,
) -> None:
    from server.routes.users_v1 import get_current_user_saas

    _, login, _ = native_runtime
    context = AsyncMock(spec=UserContext)
    context.get_user_id.return_value = str(login.principal.account_id)
    context.get_user_info.return_value = UserInfo(id=str(login.principal.account_id))
    response = await get_current_user_saas(context, expose_secrets=False)
    assert 'manage_users' in json.loads(response.body)['global_permissions']


@pytest.mark.parametrize('web_path', ['', '/openhands'])
async def test_password_tos_acceptance_needs_no_keycloak_token_interface(
    native_runtime: NativeRuntime, monkeypatch: pytest.MonkeyPatch, web_path: str
) -> None:
    _, login, _ = native_runtime
    monkeypatch.setenv('OH_WEB_URL', ORIGIN + web_path)
    auth_config.get_native_auth_settings.cache_clear()
    identity = AsyncMock(spec=user_auth_module.UserAuth)
    identity.get_user_id.return_value = str(login.principal.account_id)
    identity.get_access_token.side_effect = AssertionError('Keycloak token requested')
    with patch.object(auth, 'get_user_auth', AsyncMock(return_value=identity)):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=native_app()),
            base_url=ORIGIN,
            cookies={'openhands_session': login.token},
        ) as client:
            response = await client.post(
                '/api/accept_tos',
                json={'redirect_url': '/'},
                headers={
                    'Origin': ORIGIN,
                    'X-CSRF-Token': await get_csrf_token(client),
                },
            )
    assert response.status_code == 200
    assert response.json()['redirect_url'] == f'{ORIGIN}{web_path}/onboarding'
    identity.get_access_token.assert_not_called()


@pytest.mark.asyncio
async def test_native_token_manager_rejects_unsupported_calls_before_legacy_catches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keycloak_manager, 'ENABLE_KEYCLOAK', False)
    from server.auth.token_manager import TokenManager

    with (
        patch.object(keycloak_manager, 'KeycloakOpenID') as openid,
        patch.object(keycloak_manager, 'KeycloakAdmin') as admin,
    ):
        manager = TokenManager()
        with pytest.raises(RuntimeError, match='disabled'):
            await manager.get_keycloak_tokens('code', ORIGIN)
        with pytest.raises(RuntimeError, match='disabled'):
            await manager.disable_keycloak_user('user')
        openid.assert_not_called()
        admin.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'path',
    [
        '/api/v1/sandboxes/sandbox-1/settings/secrets',
        '/api/v1/sandboxes/sandbox-1/settings/secrets/API_KEY',
    ],
)
async def test_native_sandbox_secret_route_retains_sandbox_key_proof(
    native_runtime: NativeRuntime, path: str
) -> None:
    from fastapi import HTTPException, Response

    from openhands.app_server.sandbox import sandbox_router
    from openhands.app_server.sandbox.sandbox_models import SandboxInfo, SandboxStatus

    _, login, _ = native_runtime
    sandbox = SandboxInfo(
        id='sandbox-1',
        sandbox_spec_id='test-spec',
        session_api_key='sandbox-session-key',
        status=SandboxStatus.RUNNING,
        created_by_user_id=str(login.principal.account_id),
    )
    request = Request(
        {
            'type': 'http',
            'method': 'GET',
            'path': path,
            'headers': [(b'x-session-api-key', b'sandbox-session-key')],
        }
    )

    async def endpoint(req: Request) -> Response:
        info = await sandbox_router._valid_sandbox_from_session_key(
            req, 'sandbox-1', 'sandbox-session-key'
        )
        # Native ownership still fails closed through the ordinary context API.
        context = await sandbox_router._get_user_context(info)
        assert await context.get_user_id() == str(login.principal.account_id)
        return Response(status_code=204)

    with patch.object(
        sandbox_router, 'validate_session_key', new=AsyncMock(return_value=sandbox)
    ) as validate:
        response = await middleware.SetAuthCookieMiddleware()(request, endpoint)
        assert response.status_code == 204
        validate.assert_awaited_once_with('sandbox-session-key')
        validate.side_effect = HTTPException(401, 'Sandbox is not running')
        rejected = await middleware.SetAuthCookieMiddleware()(request, endpoint)
        assert rejected.status_code == 401


@pytest.mark.parametrize(
    'path',
    [
        '/api/organizations/members/invite/accept',
        '/oauth/device/verify-authenticated',
        '/integration/jira/workspaces/link',
        '/integration/jira-dc/workspaces/status',
        '/integration/gitlab/reinstall-webhook',
    ],
)
async def test_native_authenticated_nonstandard_mutations_require_csrf(
    native_runtime: NativeRuntime, path: str
) -> None:
    _, login, api_key = native_runtime
    app = native_app()

    @app.post(path)
    async def protected_mutation() -> dict[str, bool]:
        return {'accepted': True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=ORIGIN,
        cookies={'openhands_session': login.token},
    ) as client:
        headers = {'Origin': ORIGIN}
        if path.endswith('/oauth'):
            headers['Authorization'] = f'Bearer {api_key}'
        denied = await client.post(path, headers=headers)
        assert denied.status_code == 403
        accepted = await client.post(
            path, headers={**headers, 'X-CSRF-Token': await get_csrf_token(client)}
        )
        assert accepted.status_code == 200


async def test_native_explicit_membership_acceptance_precedes_tos_but_keeps_csrf(
    native_runtime: NativeRuntime, async_session_maker: SessionFactory
) -> None:
    _, login, _ = native_runtime
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(User)
            .where(User.id == login.principal.account_id)
            .values(accepted_tos=None)
        )
    app = native_app()

    @app.post('/api/auth/enrollment/accept-membership')
    async def accept_membership() -> dict[str, bool]:
        return {'accepted': True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=ORIGIN,
        cookies={'openhands_session': login.token},
    ) as client:
        denied = await client.post(
            '/api/auth/enrollment/accept-membership', headers={'Origin': ORIGIN}
        )
        assert denied.status_code == 403
        accepted = await client.post(
            '/api/auth/enrollment/accept-membership',
            headers={'Origin': ORIGIN, 'X-CSRF-Token': await get_csrf_token(client)},
        )
        assert accepted.status_code == 200


async def test_native_integration_oauth_is_bound_to_current_browser_session(
    native_runtime: NativeRuntime,
) -> None:
    from server.services.native_integration_auth import (
        native_integration_session,
        verify_native_integration_session,
    )

    _, login, api_key = native_runtime
    app = native_app()

    @app.post('/integration/jira/workspaces/link')
    async def start(request: Request) -> dict[str, str | None]:
        return {
            'session_id': await native_integration_session(
                request, str(login.principal.account_id)
            )
        }

    @app.get('/integration/jira/callback')
    async def callback(request: Request) -> dict[str, bool]:
        await verify_native_integration_session(
            request,
            {
                'keycloak_user_id': str(login.principal.account_id),
                'native_session_id': str(login.principal.session_id),
                'operation_type': 'workspace_link',
            },
        )
        return {'linked': True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=ORIGIN,
        cookies={'openhands_session': login.token},
    ) as client:
        started = await client.post(
            '/integration/jira/workspaces/link',
            headers={'Origin': ORIGIN, 'X-CSRF-Token': await get_csrf_token(client)},
        )
        assert started.status_code == 200
        assert started.json()['session_id'] == str(login.principal.session_id)
        accepted = await client.get('/integration/jira/callback')
        assert accepted.status_code == 200
        invalid = await client.get(
            '/integration/jira/callback', headers={'Authorization': 'Bearer invalid'}
        )
        assert invalid.status_code == 401
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        without_browser = await client.get(
            '/integration/jira/callback', headers={'Authorization': f'Bearer {api_key}'}
        )
        assert without_browser.status_code == 403


@pytest.mark.parametrize('kind', ['jira', 'jira_dc'])
async def test_native_jira_managers_resolve_local_account_and_reject_disabled(
    native_runtime: NativeRuntime,
    async_session_maker: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    from unittest.mock import MagicMock

    from integrations.jira import jira_manager
    from integrations.jira_dc import jira_dc_manager
    from server.auth import token_manager

    _, login, _ = native_runtime
    account_id = login.principal.account_id
    from integrations.jira.jira_payload import JiraEventType, JiraWebhookPayload
    from openhands.app_server.user_auth.user_auth import UserAuth
    from storage.jira_dc_user import JiraDcUser
    from storage.jira_user import JiraUser
    from storage.jira_workspace import JiraWorkspace

    store = MagicMock()
    linked: JiraUser | JiraDcUser
    authenticate: Callable[
        [], Awaitable[tuple[JiraUser | JiraDcUser | None, UserAuth | None]]
    ]
    if kind == 'jira':
        linked = JiraUser(
            keycloak_user_id=str(account_id),
            jira_user_id='atlassian-user',
            jira_workspace_id=17,
            status='active',
        )
        monkeypatch.setattr(jira_manager, 'JIRA_ENABLE_OAUTH', False)
        jira = jira_manager.JiraManager(token_manager.TokenManager())
        jira.integration_store = store
        monkeypatch.setattr(jira, '_send_error_from_payload', AsyncMock())
        payload = JiraWebhookPayload(
            event_type=JiraEventType.LABELED_TICKET,
            raw_event='jira:issue_updated',
            issue_id='42',
            issue_key='NATIVE-42',
            user_email='NATIVE@example.com',
            display_name='Native User',
            account_id='atlassian-user',
            workspace_name='native',
            base_api_url='https://native.atlassian.net',
        )
        workspace = JiraWorkspace(id=17)

        async def authenticate_jira() -> tuple[JiraUser | None, UserAuth | None]:
            return await jira._authenticate_user(payload, workspace)

        authenticate = authenticate_jira
    else:
        linked = JiraDcUser(
            keycloak_user_id=str(account_id),
            jira_dc_user_id='jira-user',
            jira_dc_workspace_id=17,
            status='active',
        )
        monkeypatch.setattr(jira_dc_manager, 'JIRA_DC_ENABLE_OAUTH', False)
        jira_dc = jira_dc_manager.JiraDcManager(token_manager.TokenManager())
        jira_dc.integration_store = store

        async def authenticate_jira_dc() -> tuple[JiraDcUser | None, UserAuth | None]:
            return await jira_dc.authenticate_user(
                'NATIVE@example.com', 'jira-user', 17
            )

        authenticate = authenticate_jira_dc
    store.get_active_user_by_keycloak_id_and_workspace = AsyncMock(return_value=linked)

    linked_user, auth = await authenticate()
    assert linked_user is linked and type(auth) is SaasUserAuth
    assert await auth.get_user_id() == str(account_id)
    store.get_active_user_by_keycloak_id_and_workspace.assert_awaited_once_with(
        str(account_id), 17
    )
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(User).where(User.id == account_id).values(is_disabled=True)
        )
    assert await authenticate() == (None, None)


@pytest.mark.asyncio
async def test_native_incompatible_adapter_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import Response

    from openhands.app_server.user_auth.default_user_auth import DefaultUserAuth
    from openhands.app_server.user_auth.user_auth import UserAuth

    async def configured_auth(request: Request) -> UserAuth:
        return DefaultUserAuth()

    monkeypatch.setattr(browser_policy, 'get_user_auth', configured_auth)
    reached_endpoint = False

    async def call_next(request: Request) -> Response:
        nonlocal reached_endpoint
        reached_endpoint = True
        return Response(status_code=204)

    request = Request(
        {
            'type': 'http',
            'method': 'GET',
            'scheme': 'https',
            'server': ('native.example.com', 443),
            'path': '/api/v1/native-test',
            'query_string': b'',
            'headers': [],
        }
    )
    response = await OpenHandsBrowserPolicy()(request, call_next)
    assert response.status_code == 401
    assert reached_endpoint is False
