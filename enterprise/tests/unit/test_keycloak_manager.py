from unittest.mock import MagicMock, patch

from server.auth import keycloak_manager
from server.auth.constants import KEYCLOAK_MAX_RETRIES, KEYCLOAK_REQUEST_TIMEOUT


def test_openid_client_uses_configured_timeout_and_retries():
    keycloak_manager._keycloak_instances.clear()

    with patch('server.auth.keycloak_manager.KeycloakOpenID') as keycloak_openid:
        keycloak_openid.return_value = MagicMock()
        keycloak_manager.get_keycloak_openid()

    assert keycloak_openid.call_args.kwargs['timeout'] == KEYCLOAK_REQUEST_TIMEOUT
    assert keycloak_openid.call_args.kwargs['max_retries'] == KEYCLOAK_MAX_RETRIES


def test_admin_client_uses_configured_timeout_and_retries():
    keycloak_manager._keycloak_admin_instances.clear()

    with patch('server.auth.keycloak_manager.KeycloakAdmin') as keycloak_admin:
        keycloak_admin.return_value = MagicMock()
        keycloak_manager.get_keycloak_admin()

    assert keycloak_admin.call_args.kwargs['timeout'] == KEYCLOAK_REQUEST_TIMEOUT
    assert keycloak_admin.call_args.kwargs['max_retries'] == KEYCLOAK_MAX_RETRIES
