"""Service account keys cannot use account-level routes (OHE-3713).

A service account authenticates only with API keys. Routes that write the
caller's own keys, secrets, settings, email or git links carry
``reject_service_account``; routes a run needs (conversations, sandbox
endpoints, reads) do not.
"""

import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request

from openhands.app_server.app_conversation.app_conversation_router import (
    router as app_conversation_router,
)
from openhands.app_server.secrets.secrets_router import router as secrets_router
from openhands.app_server.settings.settings_router import router as settings_router
from openhands.app_server.user_auth import reject_service_account
from openhands.app_server.user_auth.user_auth import AuthType
from server.auth.org_context import resolve_effective_org_id
from server.auth.saas_user_auth import SaasUserAuth
from server.routes import api_keys as api_keys_module
from server.routes.agent_profiles import router as agent_profiles_router
from server.routes.api_keys import api_router as api_keys_router
from server.routes.email import api_router as email_router
from server.routes.oauth_device import oauth_device_router
from server.routes.oauth_v2 import oauth_v2_router
from server.routes.user_app_settings import user_app_settings_router
from server.routes.users_v1 import saas_users_v1_router

# (router, method, path relative to the router's own prefix) that a service
# account must not reach.
GUARDED = [
    (api_keys_router, 'POST', '/api/keys'),
    (api_keys_router, 'DELETE', '/api/keys/{key_id}'),
    (secrets_router, 'POST', '/secrets'),
    (secrets_router, 'PUT', '/secrets/{secret_id}'),
    (secrets_router, 'DELETE', '/secrets/{secret_id}'),
    (secrets_router, 'POST', '/secrets/git-providers'),
    (secrets_router, 'DELETE', '/secrets/git-providers'),
    (settings_router, 'POST', '/settings'),
    (settings_router, 'POST', '/settings/profiles/{name}'),
    (settings_router, 'DELETE', '/settings/profiles/{name}'),
    (settings_router, 'POST', '/settings/profiles/{name}/activate'),
    (settings_router, 'POST', '/settings/profiles/{name}/rename'),
    (user_app_settings_router, 'POST', '/api/users/app'),
    (email_router, 'POST', '/api/email'),
    (oauth_v2_router, 'POST', '/oauth/{provider_id}/link'),
    (oauth_v2_router, 'DELETE', '/oauth/{provider_id}'),
    (oauth_device_router, 'POST', '/oauth/device/verify-authenticated'),
    (api_keys_router, 'GET', '/api/keys/llm/byor'),
    (api_keys_router, 'POST', '/api/keys/llm/byor/refresh'),
    (api_keys_router, 'POST', '/api/keys/llm/managed/refresh'),
    (agent_profiles_router, 'POST', '/api/agent-profiles/{profile_id}/activate'),
    (saas_users_v1_router, 'DELETE', '/api/v1/users/git-providers/{provider}'),
]


def _route(router, method: str, path: str):
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route
    raise AssertionError(f'{method} {path} not found')


@pytest.mark.parametrize(('router', 'method', 'path'), GUARDED)
def test_account_level_write_routes_reject_service_accounts(router, method, path):
    route = _route(router, method, path)

    assert reject_service_account in [d.call for d in route.dependant.dependencies]


def _app(is_service_account: bool) -> FastAPI:
    app = FastAPI()
    app.include_router(api_keys_router)

    @app.middleware('http')
    async def _authenticate(request: Request, call_next):
        request.state.user_auth = SaasUserAuth(
            user_id='11111111-1111-1111-1111-111111111111',
            refresh_token='',  # type: ignore[arg-type]
            auth_type=AuthType.BEARER,
            account_type='service' if is_service_account else 'human',
        )
        return await call_next(request)

    return app


async def test_service_account_cannot_create_api_key():
    transport = httpx.ASGITransport(app=_app(is_service_account=True))
    async with httpx.AsyncClient(transport=transport, base_url='http://t') as client:
        resp = await client.post('/api/keys', json={'name': 'persist'})

    assert resp.status_code == 403
    assert resp.json()['detail'] == 'Service accounts cannot use this endpoint'


async def test_human_passes_the_service_account_guard():
    org_id = uuid.uuid4()
    app = _app(is_service_account=False)
    app.dependency_overrides[resolve_effective_org_id] = lambda: org_id
    transport = httpx.ASGITransport(app=app)
    with patch.object(
        api_keys_module.api_key_store, 'list_api_keys', AsyncMock(return_value=[])
    ):
        async with httpx.AsyncClient(
            transport=transport, base_url='http://t'
        ) as client:
            resp = await client.delete('/api/keys/1')

    # The guard passes; the handler answers 404 because the key list is empty.
    assert resp.status_code == 404


@pytest.mark.parametrize(
    ('router', 'method', 'path'),
    [
        (app_conversation_router, 'POST', '/app-conversations'),
        (api_keys_router, 'GET', '/api/keys/current'),
        (secrets_router, 'GET', '/secrets/search'),
        (settings_router, 'GET', '/settings'),
    ],
)
def test_routes_a_run_needs_do_not_reject_service_accounts(router, method, path):
    route = _route(router, method, path)

    assert reject_service_account not in [d.call for d in route.dependant.dependencies]
