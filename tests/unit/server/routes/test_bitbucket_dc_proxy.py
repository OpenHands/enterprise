import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth.constants import (
    BITBUCKET_DC_CONNECT_TIMEOUT,
    BITBUCKET_DC_USERINFO_TIMEOUT,
)
from server.routes.bitbucket_dc_proxy import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    with patch(
        'server.routes.bitbucket_dc_proxy.BITBUCKET_DATA_CENTER_HOST', 'bitbucket.test'
    ):
        yield TestClient(app)


def assert_client_timeout(mock_client_cls):
    timeout = mock_client_cls.call_args.kwargs['timeout']
    assert timeout.connect == BITBUCKET_DC_CONNECT_TIMEOUT
    assert timeout.read == BITBUCKET_DC_USERINFO_TIMEOUT


def test_missing_authorization_header(client):
    response = client.get('/bitbucket-dc-proxy/oauth2/userinfo')
    assert response.status_code == 401
    assert response.json() == {'error': 'missing_token'}


def test_non_bearer_scheme(client):
    response = client.get(
        '/bitbucket-dc-proxy/oauth2/userinfo',
        headers={'Authorization': 'Basic xyz'},
    )
    assert response.status_code == 401
    assert response.json() == {'error': 'missing_token'}


def test_whoami_non_200(client):
    whoami_resp = MagicMock()
    whoami_resp.status_code = 403

    with patch('server.routes.bitbucket_dc_proxy.httpx.AsyncClient') as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[whoami_resp])
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        response = client.get(
            '/bitbucket-dc-proxy/oauth2/userinfo',
            headers={'Authorization': 'Bearer some_token'},
        )

    assert response.status_code == 401
    assert response.json() == {'error': 'not_authenticated'}


def test_whoami_empty_body(client):
    whoami_resp = MagicMock()
    whoami_resp.status_code = 200
    whoami_resp.text = '   '

    with patch('server.routes.bitbucket_dc_proxy.httpx.AsyncClient') as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[whoami_resp])
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        response = client.get(
            '/bitbucket-dc-proxy/oauth2/userinfo',
            headers={'Authorization': 'Bearer some_token'},
        )

    assert response.status_code == 401
    assert response.json() == {'error': 'not_authenticated'}


def test_whoami_timeout_returns_gateway_timeout(client):
    with (
        patch('server.routes.bitbucket_dc_proxy.httpx.AsyncClient') as mock_client_cls,
        patch('server.routes.bitbucket_dc_proxy.logger.warning') as mock_warning,
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ReadTimeout('timed out'))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        response = client.get(
            '/bitbucket-dc-proxy/oauth2/userinfo',
            headers={'Authorization': 'Bearer some_token'},
        )

    assert response.status_code == 504
    assert response.json() == {'error': 'bitbucket_timeout'}
    failure = mock_warning.call_args.kwargs['extra']
    assert failure['hop'] == 'whoami'
    assert failure['phase'] == 'read'
    assert failure['error_type'] == 'ReadTimeout'
    assert failure['connect_timeout_seconds'] == BITBUCKET_DC_CONNECT_TIMEOUT
    assert failure['timeout_seconds'] == BITBUCKET_DC_USERINFO_TIMEOUT
    assert failure['hop_elapsed_ms'] >= 0
    assert failure['total_elapsed_ms'] >= failure['hop_elapsed_ms']


def test_two_hops_share_one_total_timeout(client):
    whoami_resp = MagicMock(status_code=200, text='testuser')
    call_count = 0

    async def slow_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return whoami_resp
        await asyncio.Event().wait()

    with (
        patch(
            'server.routes.bitbucket_dc_proxy.BITBUCKET_DC_USERINFO_TIMEOUT',
            0.03,
        ),
        patch(
            'server.routes.bitbucket_dc_proxy.asyncio.timeout',
            wraps=asyncio.timeout,
        ) as mock_timeout,
        patch('server.routes.bitbucket_dc_proxy.httpx.AsyncClient') as mock_client_cls,
        patch('server.routes.bitbucket_dc_proxy.logger.warning') as mock_warning,
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=slow_get)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        response = client.get(
            '/bitbucket-dc-proxy/oauth2/userinfo',
            headers={'Authorization': 'Bearer some_token'},
        )

    assert response.status_code == 504
    assert response.json() == {'error': 'bitbucket_timeout'}
    assert mock_client.get.await_count == 2
    mock_timeout.assert_called_once_with(0.03)
    failure = mock_warning.call_args.kwargs['extra']
    assert failure['hop'] == 'users'
    assert failure['phase'] == 'overall'
    assert failure['error_type'] == 'TimeoutError'


def test_user_details_connection_error_returns_service_unavailable(client):
    whoami_resp = MagicMock()
    whoami_resp.status_code = 200
    whoami_resp.text = 'testuser'

    with (
        patch('server.routes.bitbucket_dc_proxy.httpx.AsyncClient') as mock_client_cls,
        patch('server.routes.bitbucket_dc_proxy.logger.warning') as mock_warning,
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                whoami_resp,
                httpx.ConnectError('connection failed'),
            ]
        )
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        response = client.get(
            '/bitbucket-dc-proxy/oauth2/userinfo',
            headers={'Authorization': 'Bearer some_token'},
        )

    assert response.status_code == 503
    assert response.json() == {'error': 'bitbucket_unavailable'}
    failure = mock_warning.call_args.kwargs['extra']
    assert failure['hop'] == 'users'
    assert failure['phase'] == 'connect'
    assert failure['error_type'] == 'ConnectError'


def test_user_details_non_200(client):
    whoami_resp = MagicMock()
    whoami_resp.status_code = 200
    whoami_resp.text = 'testuser'

    user_resp = MagicMock()
    user_resp.status_code = 404

    with patch('server.routes.bitbucket_dc_proxy.httpx.AsyncClient') as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[whoami_resp, user_resp])
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        response = client.get(
            '/bitbucket-dc-proxy/oauth2/userinfo',
            headers={'Authorization': 'Bearer some_token'},
        )

    assert response.status_code == 404
    assert response.json() == {'error': 'bitbucket_error: 404'}


def test_user_details_empty_search_results(client):
    whoami_resp = MagicMock()
    whoami_resp.status_code = 200
    whoami_resp.text = 'testuser'

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {'values': []}

    with patch('server.routes.bitbucket_dc_proxy.httpx.AsyncClient') as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[whoami_resp, user_resp])
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        response = client.get(
            '/bitbucket-dc-proxy/oauth2/userinfo',
            headers={'Authorization': 'Bearer some_token'},
        )

    assert response.status_code == 404
    assert response.json() == {'error': 'user_not_found: testuser'}


def test_happy_path_full_user_data(client):
    whoami_resp = MagicMock()
    whoami_resp.status_code = 200
    whoami_resp.text = 'jsmith'

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {
        'values': [
            {
                'id': 42,
                'name': 'jsmith',
                'displayName': 'John Smith',
                'emailAddress': 'john@example.com',
            }
        ]
    }

    with (
        patch('server.routes.bitbucket_dc_proxy.httpx.AsyncClient') as mock_client_cls,
        patch('server.routes.bitbucket_dc_proxy.logger.info') as mock_info,
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[whoami_resp, user_resp])
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        response = client.get(
            '/bitbucket-dc-proxy/oauth2/userinfo',
            headers={'Authorization': 'Bearer some_token'},
        )

    assert response.status_code == 200
    data = response.json()
    assert data['sub'] == '42'
    assert data['preferred_username'] == 'jsmith'
    assert data['name'] == 'John Smith'
    assert data['email'] == 'john@example.com'
    hop_logs = [log.kwargs['extra'] for log in mock_info.call_args_list]
    assert [(log['hop'], log['upstream_status_code']) for log in hop_logs] == [
        ('whoami', 200),
        ('users', 200),
    ]
    assert all(log['hop_elapsed_ms'] >= 0 for log in hop_logs)
    assert all(log['total_elapsed_ms'] >= log['hop_elapsed_ms'] for log in hop_logs)
    mock_client.get.assert_has_calls(
        [
            call(
                'https://bitbucket.test/plugins/servlet/applinks/whoami',
                headers={'Authorization': 'Bearer some_token'},
            ),
            call(
                'https://bitbucket.test/rest/api/latest/users',
                headers={'Authorization': 'Bearer some_token'},
                params={'filter': 'jsmith'},
            ),
        ]
    )
    assert_client_timeout(mock_client_cls)


def test_happy_path_missing_id_falls_back_to_username(client):
    whoami_resp = MagicMock()
    whoami_resp.status_code = 200
    whoami_resp.text = 'jsmith'

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {
        'values': [
            {
                'name': 'jsmith',
                'displayName': 'John Smith',
                'emailAddress': 'john@example.com',
            }
        ]
    }

    with patch('server.routes.bitbucket_dc_proxy.httpx.AsyncClient') as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[whoami_resp, user_resp])
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        response = client.get(
            '/bitbucket-dc-proxy/oauth2/userinfo',
            headers={'Authorization': 'Bearer some_token'},
        )

    assert response.status_code == 200
    assert response.json()['sub'] == 'jsmith'
    mock_client.get.assert_has_calls(
        [
            call(
                'https://bitbucket.test/plugins/servlet/applinks/whoami',
                headers={'Authorization': 'Bearer some_token'},
            ),
            call(
                'https://bitbucket.test/rest/api/latest/users',
                headers={'Authorization': 'Bearer some_token'},
                params={'filter': 'jsmith'},
            ),
        ]
    )
    assert_client_timeout(mock_client_cls)


def test_happy_path_login_name_can_differ_from_slug(client):
    whoami_resp = MagicMock()
    whoami_resp.status_code = 200
    whoami_resp.text = 'Jane.Doe@example.com'

    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.json.return_value = {
        'values': [
            {
                'id': 1,
                'name': 'other@example.com',
                'displayName': 'Other User',
                'emailAddress': 'other@example.com',
                'slug': 'other',
            },
            {
                'id': 2,
                'name': 'Jane.Doe@example.com',
                'displayName': 'Doe, Jane',
                'emailAddress': 'Jane.Doe@example.com',
                'slug': 'jane.doe_example.com',
            },
        ]
    }

    with patch('server.routes.bitbucket_dc_proxy.httpx.AsyncClient') as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[whoami_resp, user_resp])
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        response = client.get(
            '/bitbucket-dc-proxy/oauth2/userinfo',
            headers={'Authorization': 'Bearer some_token'},
        )

    assert response.status_code == 200
    data = response.json()
    assert data['sub'] == '2'
    assert data['preferred_username'] == 'Jane.Doe@example.com'
    assert data['name'] == 'Doe, Jane'
    assert data['email'] == 'Jane.Doe@example.com'
    mock_client.get.assert_has_calls(
        [
            call(
                'https://bitbucket.test/plugins/servlet/applinks/whoami',
                headers={'Authorization': 'Bearer some_token'},
            ),
            call(
                'https://bitbucket.test/rest/api/latest/users',
                headers={'Authorization': 'Bearer some_token'},
                params={'filter': 'Jane.Doe@example.com'},
            ),
        ]
    )
    assert_client_timeout(mock_client_cls)
