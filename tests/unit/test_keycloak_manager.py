from unittest.mock import MagicMock, patch

import pytest

from server.auth.constants import KEYCLOAK_MAX_RETRIES, KEYCLOAK_REQUEST_TIMEOUT
from server.auth.keycloak import manager as keycloak_manager


def test_openid_client_uses_configured_timeout_and_retries():
    keycloak_manager._keycloak_instances.clear()

    with patch('server.auth.keycloak.manager.KeycloakOpenID') as keycloak_openid:
        keycloak_openid.return_value = MagicMock()
        keycloak_manager.get_keycloak_openid()

    assert keycloak_openid.call_args.kwargs['timeout'] == KEYCLOAK_REQUEST_TIMEOUT
    assert keycloak_openid.call_args.kwargs['max_retries'] == KEYCLOAK_MAX_RETRIES


def test_admin_client_uses_configured_timeout_and_retries():
    keycloak_manager._keycloak_admin_instances.clear()

    with patch('server.auth.keycloak.manager.KeycloakAdmin') as keycloak_admin:
        keycloak_admin.return_value = MagicMock()
        keycloak_manager.get_keycloak_admin()

    assert keycloak_admin.call_args.kwargs['timeout'] == KEYCLOAK_REQUEST_TIMEOUT
    assert keycloak_admin.call_args.kwargs['max_retries'] == KEYCLOAK_MAX_RETRIES


@pytest.fixture(autouse=True)
def initialized_keycloak_mode(monkeypatch):
    from server.auth import mode

    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
