"""Public capability and import compatibility for installation auth modes."""

import os
import subprocess
import sys
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest

from server import config
from server.auth import auth_config


@pytest.fixture
def native_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(config, 'ENABLE_KEYCLOAK', False)
    monkeypatch.setattr(config, 'AUTH_MODE', 'native')
    monkeypatch.setenv('NATIVE_AUTH_APP_ORIGIN', 'https://native.example.com')
    for name in list(os.environ):
        if name.startswith(('NATIVE_GIT_', 'OH_PERMITTED_CORS_ORIGINS_')):
            monkeypatch.delenv(name)
    auth_config.get_native_auth_settings.cache_clear()
    yield
    auth_config.get_native_auth_settings.cache_clear()


@pytest.mark.asyncio
async def test_public_config_sources_share_native_capabilities(
    native_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openhands.app_server.web_client.default_web_client_config_injector import (
        DefaultWebClientConfigInjector,
    )

    monkeypatch.setattr(auth_config, 'ENABLE_KEYCLOAK', False)
    with (
        patch.object(
            config.SaaSServerConfig,
            '_get_app_slug',
            side_effect=AssertionError('GitHub lookup during local login'),
        ),
        patch(
            'openhands.app_server.web_client.default_web_client_config_injector._get_db_feature_flags',
            new=AsyncMock(return_value={}),
        ),
        patch(
            'openhands.app_server.web_client.default_web_client_config_injector._resolve_flag',
            new=AsyncMock(return_value=False),
        ),
    ):
        legacy = config.SaaSServerConfig().get_config()
        modern = await DefaultWebClientConfigInjector().get_web_client_config()
    assert legacy['auth_mode'] == modern.auth_mode
    assert legacy['login_methods'] == modern.login_methods
    assert legacy['git_connection_methods'] == modern.git_connection_methods
    assert legacy['login_methods'] == ['password']
    assert legacy['git_connection_methods'] == {
        'github': ['pat'],
        'gitlab': ['pat'],
        'bitbucket': ['api_token'],
    }
    assert legacy['PROVIDERS_CONFIGURED'] == modern.providers_configured == []
    assert 'AUTH_URL' not in legacy
    assert modern.auth_url is None


def test_native_cors_accepts_explicit_indexed_origins_and_rejects_wildcards(
    native_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('OH_PERMITTED_CORS_ORIGINS_0', 'https://console.example.com')
    assert config.get_native_cors_origins() == [
        'https://native.example.com',
        'https://console.example.com',
    ]
    monkeypatch.setenv('OH_PERMITTED_CORS_ORIGINS_0', '*')
    with pytest.raises(ValueError, match='explicit trusted'):
        config.get_native_cors_origins()


@pytest.mark.parametrize(
    'name,value',
    [
        ('OH_APP_MODE', 'openhands'),
        (
            'OH_USER_KIND',
            'openhands.app_server.user.specifiy_user_context.SpecifyUserContext',
        ),
        (
            'OH_LIFESPAN_KIND',
            'openhands.app_server.app_lifespan.oss_app_lifespan_service.OssAppLifespanService',
        ),
        (
            'OPENHANDS_CONFIG_CLS',
            'openhands.app_server.server_config.server_config.ServerConfig',
        ),
    ],
)
def test_native_rejects_incompatible_known_class_selectors(
    native_config: None, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        config.validate_native_auth_configuration()


@pytest.mark.parametrize('mode', ['false', '0'])
def test_native_app_import_never_constructs_keycloak(mode: str) -> None:
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(
            (
                'KEYCLOAK_',
                'OH_',
                'OPENHANDS_',
                'NATIVE_GIT_',
                'GITHUB_APP_',
                'GITLAB_APP_',
                'BITBUCKET_APP_',
            )
        )
    }
    env.update(
        ENABLE_KEYCLOAK=mode, NATIVE_AUTH_APP_ORIGIN='https://native.example.com'
    )
    script = """
from unittest.mock import patch
with patch('keycloak.keycloak_admin.KeycloakAdmin', side_effect=AssertionError('Keycloak admin initialized')), patch('keycloak.keycloak_openid.KeycloakOpenID', side_effect=AssertionError('Keycloak OIDC initialized')):
    import saas_server
    from openhands.app_server.config import get_global_config
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService
    assert isinstance(get_global_config().lifespan, SaasAppLifespanService)
    assert get_global_config().app_mode.value == 'saas'
    routes = {getattr(route, 'path', '') for route in saas_server.app.routes}
    assert '/api/auth/password/login' in routes
    assert '/oauth/keycloak/callback' not in routes
"""
    result = subprocess.run(
        [sys.executable, '-c', script],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr[-5000:]


def test_native_explicit_oauth_requires_complete_registration(
    native_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('NATIVE_GIT_GITHUB_OAUTH_ENABLED', '1')
    monkeypatch.setenv('GITHUB_APP_CLIENT_ID', 'public-client')
    monkeypatch.delenv('GITHUB_APP_CLIENT_SECRET', raising=False)
    with pytest.raises(ValueError, match='complete registration'):
        config.get_auth_capabilities()
    monkeypatch.setenv('GITHUB_APP_CLIENT_SECRET', 'test-secret')
    assert config.get_auth_capabilities()['git_connection_methods']['github'] == [
        'pat',
        'oauth',
    ]
