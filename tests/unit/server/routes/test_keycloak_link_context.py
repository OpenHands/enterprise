from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from pydantic import SecretStr

from server.auth.login_policy import (
    enforce_enterprise_sso,
    require_native_login_allowed,
)
from server.auth.token_manager import KeycloakUserInfo
from server.routes.auth import keycloak_link_context


@pytest.mark.parametrize(
    'marker, expected',
    [
        ('enterprise_sso:saml', 'enterprise_sso'),
        ('github', 'github'),
        ('azure_devops', 'azure_devops'),
        ('unknown', None),
        (None, None),
    ],
)
@pytest.mark.asyncio
async def test_server_hint_uses_authenticated_user_info(monkeypatch, marker, expected):
    monkeypatch.delenv('ENFORCE_ENTERPRISE_SSO', raising=False)
    with (
        patch(
            'server.routes.auth.get_access_token',
            AsyncMock(return_value=SecretStr('test')),
        ),
        patch(
            'server.routes.auth.token_manager.get_user_info',
            AsyncMock(
                return_value=KeycloakUserInfo(sub='user', identity_provider=marker)
            ),
        ),
    ):
        result = await keycloak_link_context(MagicMock(spec=Request))
    assert result.provider == expected


@pytest.mark.asyncio
async def test_link_context_requires_authentication():
    with patch('server.routes.auth.get_access_token', AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await keycloak_link_context(MagicMock(spec=Request))
    assert exc.value.status_code == 401


@pytest.mark.parametrize('value', ['true', '1'])
@pytest.mark.asyncio
async def test_enforcement_overrides_a_stale_provider_marker(monkeypatch, value):
    monkeypatch.setenv('ENFORCE_ENTERPRISE_SSO', value)
    with (
        patch(
            'server.routes.auth.get_access_token',
            AsyncMock(return_value=SecretStr('test')),
        ),
        patch(
            'server.routes.auth.token_manager.get_user_info',
            AsyncMock(
                return_value=KeycloakUserInfo(sub='user', identity_provider='github')
            ),
        ),
    ):
        result = await keycloak_link_context(MagicMock(spec=Request))
    assert result.provider == 'enterprise_sso'
    assert enforce_enterprise_sso()
    with pytest.raises(HTTPException) as exc:
        require_native_login_allowed()
    assert exc.value.status_code == 403


@pytest.mark.parametrize('value', ['false', '0', ''])
def test_other_login_stack_remains_available_by_default(monkeypatch, value):
    monkeypatch.setenv('ENFORCE_ENTERPRISE_SSO', value)
    assert not enforce_enterprise_sso()
    require_native_login_allowed()


@pytest.mark.asyncio
async def test_existing_native_session_refresh_still_remints_cookie(monkeypatch):
    from types import SimpleNamespace

    from fastapi import Response

    from openhands.app_server.user_auth.user_auth import AuthType
    from server.middleware import SetAuthCookieMiddleware

    monkeypatch.setenv('ENFORCE_ENTERPRISE_SSO', 'true')
    request = Request(
        {
            'type': 'http',
            'method': 'POST',
            'path': '/api/example',
            'headers': [(b'cookie', b'openhands_auth=old')],
            'scheme': 'https',
            'server': ('app.example.test', 443),
            'query_string': b'',
        }
    )
    request.state.user_auth = SimpleNamespace(
        auth_type=AuthType.COOKIE,
        oauth_v2_cookie=True,
        refreshed=True,
        user_id='existing-user',
        access_token_expires_at=None,
        accepted_tos=True,
        idp_provider_id=1,
        idp_refresh_token_expires_at=None,
        get_user_id=AsyncMock(return_value=None),
    )
    response = Response('route completed')
    next_route = AsyncMock(return_value=response)
    middleware = SetAuthCookieMiddleware()
    with (
        patch.object(middleware, '_should_attach', return_value=False),
        patch(
            'server.auth.oauth_v2_refresh.sign_oauth_v2_cookie',
            return_value='refreshed-cookie',
        ),
    ):
        result = await middleware(request, next_route)
    next_route.assert_awaited_once_with(request)
    assert result.status_code == 200
    assert result.body == b'route completed'
    assert 'openhands_auth=refreshed-cookie' in result.headers['set-cookie']


@pytest.mark.asyncio
async def test_native_login_denied_before_user_lookup_or_creation(monkeypatch):
    from server.auth.provider_identity import resolve_or_create_user

    monkeypatch.setenv('ENFORCE_ENTERPRISE_SSO', '1')
    with patch('server.auth.provider_identity.AuthProviderUserStore') as store:
        with pytest.raises(HTTPException) as exc:
            await resolve_or_create_user(MagicMock(), {'sub': 'native-user'})
    assert exc.value.status_code == 403
    store.assert_not_called()


@pytest.mark.asyncio
async def test_password_idp_denied_before_provider_lookup(monkeypatch):
    from server.routes.idp import _require_idp_available

    monkeypatch.setenv('ENFORCE_ENTERPRISE_SSO', 'true')
    with patch('server.routes.idp._get_integrated_idp_provider') as lookup:
        with pytest.raises(HTTPException) as exc:
            await _require_idp_available()
    assert exc.value.status_code == 403
    lookup.assert_not_called()


def test_password_idp_cannot_mint_a_cookie_under_policy(monkeypatch):
    from server.routes.idp import _set_idp_cookie

    monkeypatch.setenv('ENFORCE_ENTERPRISE_SSO', 'true')
    response = MagicMock()
    with pytest.raises(HTTPException) as exc:
        _set_idp_cookie(MagicMock(spec=Request), response, 'user', True, True, 1)
    assert exc.value.status_code == 403
    response.set_cookie.assert_not_called()


@pytest.mark.asyncio
async def test_integrated_password_status_is_unavailable_under_policy(monkeypatch):
    from server.routes.idp import is_idp_available

    monkeypatch.setenv('ENFORCE_ENTERPRISE_SSO', 'true')
    with patch('server.routes.idp._get_integrated_idp_provider') as lookup:
        assert not await is_idp_available()
    lookup.assert_not_called()


@pytest.mark.parametrize(
    'kind, status', [('authentication', 401), ('connection', 503), ('claims', 401)]
)
@pytest.mark.asyncio
async def test_link_context_reports_auth_or_service_failure(kind, status):
    from keycloak.exceptions import KeycloakAuthenticationError, KeycloakConnectionError
    from pydantic import ValidationError

    if kind == 'claims':
        try:
            KeycloakUserInfo.model_validate({})
        except ValidationError as exc:
            error = exc
    else:
        error = (
            KeycloakAuthenticationError()
            if kind == 'authentication'
            else KeycloakConnectionError()
        )
    with (
        patch(
            'server.routes.auth.get_access_token',
            AsyncMock(return_value=SecretStr('test')),
        ),
        patch(
            'server.routes.auth.token_manager.get_user_info',
            AsyncMock(side_effect=error),
        ),
    ):
        with pytest.raises(HTTPException) as caught:
            await keycloak_link_context(MagicMock(spec=Request))
    assert caught.value.status_code == status


@pytest.mark.parametrize('policy', ['true', 'false'])
@pytest.mark.asyncio
async def test_policy_reaches_web_client_without_replacing_structured_flags(
    monkeypatch, policy
):
    from types import SimpleNamespace

    from openhands.app_server.types import AppMode
    from openhands.app_server.web_client import (
        default_web_client_config_injector as mod,
    )
    from openhands.app_server.web_client.web_client_models import WebClientFeatureFlags

    monkeypatch.setenv('ENFORCE_ENTERPRISE_SSO', policy)
    flags = WebClientFeatureFlags(
        enable_jira=True,
        enable_jira_dc=True,
        enable_linear=True,
        enable_super_admin=True,
    )
    with (
        patch(
            'openhands.app_server.config.get_global_config',
            return_value=SimpleNamespace(app_mode=AppMode.SAAS),
        ),
        patch.object(mod, '_resolve_flag', AsyncMock(return_value=False)),
        patch.object(mod, '_get_db_feature_flags', AsyncMock(return_value={})),
        patch.object(
            mod, '_resolve_enable_integrated_idp', AsyncMock(return_value=False)
        ),
    ):
        config = await mod.DefaultWebClientConfigInjector(
            feature_flags=flags
        ).get_web_client_config()
    assert config.feature_flags.enforce_enterprise_sso == (policy == 'true')
    assert config.feature_flags.enable_jira
    assert config.feature_flags.enable_jira_dc
    assert config.feature_flags.enable_linear
    assert config.feature_flags.enable_super_admin
