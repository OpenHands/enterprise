"""The same public transport contract runs against every real proxy."""

import hashlib
import hmac
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from websockets.sync.client import connect

from server.auth.cookie_chunking import CHUNK_SIZE, MAX_CHUNKS

PREFIX = '/api/automation'


@pytest.mark.parametrize(
    'path,role',
    [
        (PREFIX, 'automation'),
        (PREFIX + '/v1/runs?tag=a&tag=b', 'automation'),
        (PREFIX + '-other', 'enterprise'),
        ('/automations', 'enterprise'),
        ('/canvas/automations', 'enterprise'),
        ('/api/v1/users/me', 'enterprise'),
    ],
)
def test_route_and_query_are_preserved(proxy, path, role):
    response = proxy.client.get(path)
    assert response.status_code == 200
    assert response.json()['role'] == role
    assert response.json()['path'] == path.split('?')[0]
    assert response.json()['query'] == (path.split('?', 1)[1] if '?' in path else '')


def test_credentials_and_forwarded_origin(proxy):
    cookie = '; '.join(
        f'keycloak_auth{"_" + str(i) if i else ""}={"x" * CHUNK_SIZE}'
        for i in range(MAX_CHUNKS)
    )
    headers = {
        'Cookie': cookie,
        'Authorization': 'Bearer synthetic-test-token',
        'X-Session-API-Key': 'synthetic-test-key',
        'X-Org-Id': 'synthetic-test-org',
        'X-Forwarded-Proto': 'http',
        'X-Forwarded-For': '203.0.113.66',
    }
    response = proxy.client.get(PREFIX + '/echo', headers=headers)
    assert response.status_code == 200
    observed = response.json()
    for name in ('Cookie', 'Authorization', 'X-Session-API-Key', 'X-Org-Id'):
        assert observed['headers'][name.lower()] == headers[name]
    assert observed['scheme'] == 'https'
    assert observed['headers']['host'] == proxy.client.base_url.netloc.decode()
    assert '203.0.113.66' not in observed['headers']['x-forwarded-for']


def test_signed_body_and_rejection_are_preserved(proxy):
    body = b'{ "event": "synthetic", "unicode": "\\u00e9" }\n'
    signature = 'sha256=' + hmac.new(b'fixture-only', body, hashlib.sha256).hexdigest()
    for sent_signature, status in ((signature, 202), ('sha256=invalid', 401)):
        response = proxy.client.post(
            PREFIX + '/signed',
            content=body,
            headers={'X-Hub-Signature-256': sent_signature},
        )
        assert response.status_code == status
    assert proxy.client.post(PREFIX + '/signed', content=body).status_code == 401


def test_two_megabyte_upload_is_unchanged(proxy):
    body = bytes(range(256)) * 8192
    response = proxy.client.post(PREFIX + '/upload', content=body)
    assert response.status_code == 200
    assert response.json() == {
        'size': len(body),
        'sha256': hashlib.sha256(body).hexdigest(),
    }


def test_redirect_and_separate_set_cookie_headers(proxy):
    response = proxy.client.get(PREFIX + '/redirect')
    assert response.status_code == 307
    assert (
        response.headers['location']
        == str(proxy.client.base_url).rstrip('/') + PREFIX + '/echo'
    )
    cookies = response.headers.get_list('set-cookie')
    assert len(cookies) == 2
    assert cookies[0].startswith('session=synthetic;')
    assert cookies[1].startswith('org=synthetic;')
    assert all('Secure' in cookie and 'HttpOnly' in cookie for cookie in cookies)


def test_sse_delivers_first_event_before_producer_finishes(proxy):
    started = time.monotonic()
    with proxy.client.stream('GET', PREFIX + '/events') as response:
        assert response.status_code == 200
        lines = response.iter_lines()
        assert next(lines) == 'data: first'
        assert time.monotonic() - started < 2
        assert list(lines) == ['', 'data: last', '']


def test_websocket_echo_survives_config_reload(proxy):
    url = str(proxy.client.base_url).replace('https:', 'wss:').rstrip('/') + '/ws'
    with connect(url, ssl=proxy.tls, proxy=None, open_timeout=5) as websocket:
        websocket.send('before reload')
        assert websocket.recv(timeout=3) == 'enterprise:before reload'
        proxy.reload()
        websocket.send('after reload')
        assert websocket.recv(timeout=3) == 'enterprise:after reload'
    assert proxy.client.get('/echo').status_code == 200


def test_paused_automation_does_not_block_enterprise(proxy):
    proxy.automation.pause()
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            pending = [
                pool.submit(proxy.client.get, PREFIX + '/echo') for _ in range(8)
            ]
            time.sleep(0.2)
            assert all(not future.done() for future in pending)
            started = time.monotonic()
            responses = [proxy.client.get('/echo') for _ in range(10)]
            assert all(
                r.status_code == 200 and r.json()['role'] == 'enterprise'
                for r in responses
            )
            assert time.monotonic() - started < 3
            assert all(f.result(timeout=10).status_code == 504 for f in pending)
    finally:
        proxy.automation.unpause()
    proxy.wait_ready()


def test_proxy_can_start_with_automation_stopped(proxy):
    proxy.automation.stop(timeout=1)
    try:
        proxy.container.restart(timeout=1)
        proxy.refresh_client()
        proxy.wait_ready(automation=False)
        assert proxy.client.get('/echo').json()['role'] == 'enterprise'
        assert proxy.client.get(PREFIX + '/echo').status_code in (502, 503, 504)
    finally:
        proxy.automation.start()
        proxy.wait_ready()
