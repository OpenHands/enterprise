"""The config endpoint consumed by the login UI must select the backend realm."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.app_server.types import AppMode
from openhands.app_server.web_client.default_web_client_config_injector import (
    DefaultWebClientConfigInjector,
)
from openhands.app_server.web_client.web_client_router import router


@pytest.mark.parametrize(
    'realm,host,expected',
    [
        ('allhands', 'https://auth.example.com', 'https://auth.example.com'),
        ('', 'https://auth.example.com', 'https://auth.example.com'),
        (None, 'https://auth.example.com', 'https://auth.example.com'),
        (
            'allhands-oidc',
            'https://auth.example.com',
            'https://auth.example.com/realms/allhands-oidc',
        ),
        (
            'company realm',
            'https://auth.example.com/auth/',
            'https://auth.example.com/auth/realms/company%20realm',
        ),
        ('allhands-oidc', None, None),
    ],
)
def test_login_config_endpoint_exposes_selected_realm(
    monkeypatch, realm, host, expected
):
    if realm is None:
        monkeypatch.delenv('KEYCLOAK_REALM_NAME', raising=False)
    else:
        monkeypatch.setenv('KEYCLOAK_REALM_NAME', realm)
    if host:
        monkeypatch.setenv('AUTH_URL', host)
    else:
        monkeypatch.delenv('AUTH_URL', raising=False)
    injector = DefaultWebClientConfigInjector()
    config = SimpleNamespace(web_client=injector, app_mode=AppMode.SAAS)
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    module = 'openhands.app_server.web_client.default_web_client_config_injector'
    with (
        patch('openhands.app_server.config.get_global_config', return_value=config),
        patch(
            'openhands.app_server.web_client.web_client_router.get_global_config',
            return_value=config,
        ),
        patch(module + '._get_db_feature_flags', new=AsyncMock(return_value={})),
        patch(module + '._resolve_flag', new=AsyncMock(return_value=False)),
        patch(
            module + '._resolve_enable_integrated_idp',
            new=AsyncMock(return_value=False),
        ),
        TestClient(app) as client,
    ):
        response = client.get('/api/v1/web-client/config')
    assert response.status_code == 200
    assert response.json()['auth_url'] == expected
    assert injector.auth_url == host
    assert __import__('os').getenv('AUTH_URL') == host
