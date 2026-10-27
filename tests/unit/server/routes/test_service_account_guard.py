"""Service account keys cannot use account-level routes (OHE-3713).

A service account authenticates only with API keys. Routes that write the
caller's own keys, secrets, settings, email or git links carry
``reject_service_account``; routes a run needs (conversations, sandbox
endpoints, reads) do not.
"""

import json
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
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
from server.auth.authorization import ROLE_PERMISSIONS, RoleName
from server.auth.org_context import resolve_effective_org_id
from server.auth.saas_user_auth import SaasUserAuth
from server.routes import api_keys as api_keys_module
from server.routes.agent_profiles import router as agent_profiles_router
from server.routes.api_keys import api_router as api_keys_router
from server.routes.billing import billing_router
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
    (oauth_v2_router, 'POST', '/oauth/git-providers/{provider_id}/check'),
    (oauth_device_router, 'POST', '/oauth/device/verify-authenticated'),
    (billing_router, 'POST', '/api/billing/create-customer-setup-session'),
    (billing_router, 'POST', '/api/billing/create-checkout-session'),
    (api_keys_router, 'GET', '/api/keys/llm/byor'),
    (api_keys_router, 'POST', '/api/keys/llm/byor/refresh'),
    (api_keys_router, 'POST', '/api/keys/llm/managed/refresh'),
    (agent_profiles_router, 'POST', '/api/agent-profiles/{profile_id}/activate'),
    # A personal-scope write needs only membership, which the role has.
    (agent_profiles_router, 'POST', '/api/agent-profiles/{name}'),
    (agent_profiles_router, 'DELETE', '/api/agent-profiles/{name}'),
    (agent_profiles_router, 'POST', '/api/agent-profiles/{name}/rename'),
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


# Every mutating route on the real SaaS app must be classified. A route passes
# when it (a) has ``reject_service_account``, (b) requires a permission the
# service_account role lacks, or (c) is listed here with the reason a service
# account run may call it or cannot affect the account. A new route fails
# ``test_every_mutating_route_is_classified`` until it is guarded or listed.
_RUN_NEEDS = 'a service account run needs it (conversation, sandbox or MCP call)'
_NO_AUTH = 'unauthenticated or signed by a third party; no caller identity'
_PROVIDER = 'needs a git or Jira provider identity, which a service account lacks'
_ROLE_CHECK = (
    'the service layer needs an owner or admin role; service_account has neither'
)
_NO_IDENTITY = 'needs an email, password or IdP login, which a service account lacks'
MUTATING_ROUTE_ALLOWLIST: dict[tuple[str, str], str] = {
    ('POST', '/api/v1/app-conversations'): _RUN_NEEDS,
    ('PATCH', '/api/v1/app-conversations/{conversation_id}'): _RUN_NEEDS,
    ('POST', '/api/v1/app-conversations/{conversation_id}/send-message'): _RUN_NEEDS,
    ('POST', '/api/v1/app-conversations/{conversation_id}/switch_profile'): _RUN_NEEDS,
    (
        'POST',
        '/api/v1/app-conversations/{conversation_id}/switch_acp_model',
    ): _RUN_NEEDS,
    ('DELETE', '/api/v1/app-conversations/{conversation_id}'): _RUN_NEEDS,
    ('POST', '/api/v1/app-conversations/stream-start'): _RUN_NEEDS,
    ('POST', '/api/v1/conversations/{conversation_id}/pending-messages'): _RUN_NEEDS,
    ('POST', '/api/v1/sandboxes'): _RUN_NEEDS,
    ('POST', '/api/v1/sandboxes/{sandbox_id}/pause'): _RUN_NEEDS,
    ('POST', '/api/v1/sandboxes/{sandbox_id}/resume'): _RUN_NEEDS,
    ('DELETE', '/api/v1/sandboxes/{sandbox_id}'): _RUN_NEEDS,
    ('POST', '/api/v1/skills/marketplace-skills'): 'reads skill metadata for a run',
    ('POST', '/api/v1/skills/marketplace-check'): 'reads skill metadata for a run',
    ('POST', '/api/v1/mcp/test'): 'probes an MCP server; stores nothing on the account',
    ('POST', '/api/v1/mcp/servers/{server_name}/call-tool'): _RUN_NEEDS,
    (
        'POST',
        '/api/v1/mcp/oauth/start',
    ): 'starts a probe job; stores nothing on the account',
    (
        'POST',
        '/api/v1/webhooks/conversations',
    ): 'sandbox callback, authenticated by session key',
    (
        'POST',
        '/api/v1/webhooks/events/{conversation_id}',
    ): 'sandbox callback, authenticated by session key',
    (
        'POST',
        '/api/v1/webhooks/mcp-oauth-state',
    ): 'sandbox callback, authenticated by session key',
    (
        'POST',
        '/api/v1/webhooks/conversations/{conversation_id}/children',
    ): 'sandbox callback, authenticated by session key',
    (
        'POST',
        '/api/agent-profiles/{name}/materialize',
    ): 'dry run; reads the profile and writes nothing',
    (
        'POST',
        '/api/accept_tos',
    ): 'sets the caller accepted_tos flag only; cannot change the account',
    (
        'POST',
        '/api/complete_onboarding',
    ): 'sets the caller onboarding flag only; cannot change the account',
    (
        'POST',
        '/api/organizations/{org_id}/switch',
    ): 'sets the caller current org to one it is a member of',
    ('POST', '/api/keycloak/link-context'): 'returns a routing hint; changes nothing',
    ('POST', '/api/authenticate'): 'checks the session; changes nothing',
    ('POST', '/api/logout'): 'clears the caller own cookie',
    ('POST', '/api/analytics/events'): 'records a frontend analytics event',
    ('POST', '/api/quota/increase-request'): 'answers 410 Gone',
    ('POST', '/api/billing/has-payment-method'): 'reads billing state',
    ('POST', '/oauth/device/authorize'): _NO_AUTH,
    ('POST', '/oauth/device/token'): _NO_AUTH,
    ('POST', '/oauth/idp/login'): _NO_AUTH,
    ('POST', '/oauth/idp/signup'): _NO_AUTH,
    ('POST', '/oauth/idp/invite'): _NO_AUTH,
    ('POST', '/saml/{provider_id}/acs'): _NO_AUTH,
    ('POST', '/slack/on-event'): _NO_AUTH,
    ('POST', '/slack/on-options-load'): _NO_AUTH,
    ('POST', '/slack/on-form-interaction'): _NO_AUTH,
    ('POST', '/integration/github/events'): _NO_AUTH,
    ('POST', '/integration/gitlab/events'): _NO_AUTH,
    ('POST', '/integration/bitbucket/events'): _NO_AUTH,
    ('POST', '/integration/azure-devops/events'): _NO_AUTH,
    ('POST', '/integration/jira/events'): _NO_AUTH,
    ('POST', '/integration/jira-dc/connections/{workspace_id}/events'): _NO_AUTH,
    ('POST', '/integration/bitbucket-dc/connections/{connection_id}/events'): _NO_AUTH,
    (
        'POST',
        '/api/service/users/{user_id}/orgs/{org_id}/api-keys',
    ): 'automations service route, authenticated by the service key',
    (
        'DELETE',
        '/api/service/users/{user_id}/orgs/{org_id}/api-keys/{key_name}',
    ): 'automations service route, authenticated by the service key',
    ('POST', '/integration/gitlab/reinstall-webhook'): _PROVIDER,
    ('POST', '/integration/jira/workspaces/unlink'): _PROVIDER,
    ('POST', '/integration/jira-dc/workspaces/status'): _PROVIDER,
    ('POST', '/integration/jira-dc/workspaces/unlink'): _PROVIDER,
    ('POST', '/api/organizations/{org_id}/members/invite'): _ROLE_CHECK,
    ('DELETE', '/api/organizations/{org_id}/members/{user_id}'): _ROLE_CHECK,
    ('PATCH', '/api/organizations/{org_id}/members/{user_id}'): _ROLE_CHECK,
    (
        'POST',
        '/api/organizations/members/invite/accept',
    ): 'needs an invitation token sent to an email; a service account has no email',
    (
        'POST',
        '/api/admin/verified-models',
    ): 'needs an admin email domain; a service account has no email',
    (
        'PUT',
        '/api/admin/verified-models/{provider}/{model_name:path}',
    ): 'needs an admin email domain; a service account has no email',
    (
        'DELETE',
        '/api/admin/verified-models/{provider}/{model_name:path}',
    ): 'needs an admin email domain; a service account has no email',
    (
        'POST',
        '/api/idp/signup-links',
    ): 'checks CREATE_SIGNUP_LINK in the handler; service_account lacks it',
    ('PUT', '/api/email/resend'): _NO_IDENTITY,
}

# Prints one JSON row per mutating route of the real SaaS app, with every
# integration router enabled. It runs in a subprocess because importing
# ``saas_server`` sets process-wide environment and mounts the whole app.
_SCAN_SCRIPT = textwrap.dedent(
    """
    import json
    from fastapi.routing import APIRoute
    from openhands.app_server.user_auth import reject_service_account
    import saas_server

    def calls(dependant):
        for dep in dependant.dependencies:
            yield dep.call
            yield from calls(dep)

    rows = []
    for route in saas_server.app.router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(set(route.methods or ()) & {'POST', 'PUT', 'PATCH', 'DELETE'}):
            deps = list(calls(route.dependant))
            rows.append({
                'method': method,
                'path': route.path,
                'rejects': reject_service_account in deps,
                'permissions': [
                    c.__closure__[0].cell_contents.name
                    for c in deps
                    if 'require_permission' in getattr(c, '__qualname__', '')
                ],
            })
    print('ROWS=' + json.dumps(rows))
    """
)


def _scan_saas_routes(tmp_path: Path) -> list[dict]:
    frontend_build = tmp_path / 'frontend'
    frontend_build.mkdir()
    (frontend_build / 'index.html').write_text('<html></html>')
    repo_root = Path(__file__).resolve().parents[4]
    env = os.environ.copy()
    env.update(
        PYTHONPATH=f'{repo_root}:{env.get("PYTHONPATH", "")}',
        OPENHANDS_SUPPRESS_BANNER='1',
        POSTHOG_CLIENT_KEY='test-posthog-key',
        FRONTEND_DIRECTORY=str(frontend_build),
        GITHUB_APP_CLIENT_ID='x',
        GITHUB_APP_PRIVATE_KEY='x',
        GITLAB_APP_CLIENT_ID='x',
        BITBUCKET_APP_CLIENT_ID='x',
        AZURE_DEVOPS_CLIENT_ID='x',
        ENABLE_JIRA='true',
        ENABLE_JIRA_DC='true',
        BITBUCKET_DATA_CENTER_HOST='bitbucket.example.com',
    )
    result = subprocess.run(
        [sys.executable, '-c', _SCAN_SCRIPT],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(ln for ln in result.stdout.splitlines() if ln.startswith('ROWS='))
    return json.loads(line.removeprefix('ROWS='))


def test_every_mutating_route_is_classified(tmp_path):
    sa_permissions = {p.name for p in ROLE_PERMISSIONS[RoleName.SERVICE_ACCOUNT]}
    rows = _scan_saas_routes(tmp_path)
    assert len(rows) > 100  # the scan found the real app

    unclassified = []
    for row in rows:
        key = (row['method'], row['path'])
        needs_permission_sa_lacks = any(
            name not in sa_permissions for name in row['permissions']
        )
        if (
            row['rejects']
            or needs_permission_sa_lacks
            or key in MUTATING_ROUTE_ALLOWLIST
        ):
            continue
        unclassified.append(key)

    assert not unclassified, (
        'Mutating routes a service account key could call. Add '
        'Depends(reject_service_account), require a permission the '
        'service_account role lacks, or list the route in '
        f'MUTATING_ROUTE_ALLOWLIST with a reason: {sorted(unclassified)}'
    )

    scanned = {(row['method'], row['path']) for row in rows}
    assert not set(MUTATING_ROUTE_ALLOWLIST) - scanned, 'stale allowlist entries'
    guarded = {
        (row['method'], row['path'])
        for row in rows
        if row['rejects']
        or any(name not in sa_permissions for name in row['permissions'])
    }
    assert not set(MUTATING_ROUTE_ALLOWLIST) & guarded, 'guarded routes need no entry'
