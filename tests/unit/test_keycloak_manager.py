from unittest.mock import MagicMock, patch

import pytest

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


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('client_id', ['', 'openhands-provisioner'])
@pytest.mark.parametrize('client_secret', ['', 'separate-service-secret'])
def test_admin_auth_and_refresh_realm(monkeypatch, external, client_id, client_secret):
    from keycloak.keycloak_admin import KeycloakAdmin

    keycloak_manager._keycloak_admin_instances.clear()
    monkeypatch.setattr(keycloak_manager, 'KEYCLOAK_ADMIN_CLIENT_ID', client_id)
    monkeypatch.setattr(
        keycloak_manager, 'KEYCLOAK_ADMIN_CLIENT_SECRET', client_secret, raising=False
    )
    monkeypatch.setattr(keycloak_manager, 'KEYCLOAK_ADMIN_PASSWORD', 'test-secret')
    monkeypatch.setattr(keycloak_manager, 'KEYCLOAK_REALM_NAME', 'allhands')
    monkeypatch.setattr(
        keycloak_manager, 'KEYCLOAK_SERVER_URL', 'https://internal.test/'
    )
    monkeypatch.setattr(
        keycloak_manager, 'KEYCLOAK_SERVER_URL_EXT', 'https://external.test/'
    )

    with patch.object(KeycloakAdmin, 'get_realm', return_value={'realm': 'allhands'}):
        client = keycloak_manager.get_keycloak_admin(external)
    connection = client.connection
    assert connection.realm_name == 'allhands'
    assert connection.keycloak_openid.realm_name == 'master'
    assert connection.server_url == (
        'https://external.test/' if external else 'https://internal.test/'
    )
    assert connection.grant_type == ('client_credentials' if client_id else 'password')
    assert connection.client_id == (client_id or 'admin-cli')
    assert connection.username == (None if client_id else 'admin')
    assert connection.password == (None if client_id else 'test-secret')
    assert connection.client_secret_key == (
        (client_secret or 'test-secret') if client_id else None
    )
    assert connection.verify is True
    with patch.object(
        connection.keycloak_openid,
        'token',
        return_value={'access_token': 'test-token', 'expires_in': 300},
    ) as token:
        connection.get_token()
        connection.refresh_token()
    assert len(token.call_args_list) == 2
    for call in token.call_args_list:
        assert call.kwargs['grant_type'] == connection.grant_type
    assert keycloak_manager.get_keycloak_admin(external) is client
    keycloak_manager._keycloak_admin_instances.clear()


def test_whitespace_client_id_keeps_legacy_admin_auth(monkeypatch):
    import importlib

    from server.auth import constants

    try:
        with monkeypatch.context() as env:
            env.setenv('KEYCLOAK_ADMIN_CLIENT_ID', '  ')
            importlib.reload(constants)
            assert constants.KEYCLOAK_ADMIN_CLIENT_ID == ''
    finally:
        importlib.reload(constants)
