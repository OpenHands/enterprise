"""Opt-in checks against an already running, disposable real-app Compose stack.

Set REAL_STACK_URL, REAL_STACK_CA and REAL_STACK_PROJECT. Fault tests act only
on the automation container in that explicitly selected Compose project.
"""

import os
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import docker
import httpx
import pytest


@pytest.fixture(scope='module')
def client():
    url = os.getenv('REAL_STACK_URL')
    if not url:
        pytest.skip('opt-in: set REAL_STACK_URL and REAL_STACK_CA')
    tls = ssl.create_default_context(cafile=os.environ['REAL_STACK_CA'])
    with httpx.Client(base_url=url, verify=tls, trust_env=False, timeout=20) as c:
        yield c


def test_actual_applications_and_databases_are_reachable(client):
    assert client.get('/saas').json() == {'saas': True}
    assert client.get('/ready').status_code == 200
    assert client.get('/api/automation/ready').json() == {'status': 'ready'}
    assert 'text/html' in client.get('/').headers['content-type']
    assert client.get('/api/automation/openapi.json').json()['info']['title']


def test_missing_credentials_are_rejected_by_real_applications(client):
    assert client.get('/api/v1/users/me').status_code in (401, 403)
    assert client.get('/api/automation/v1').status_code in (401, 403)


def test_prefix_boundary_does_not_route_to_automation(client):
    assert client.get('/api/automation-other/ready').status_code != 200


def test_automation_redirect_retains_https_origin(client):
    response = client.get('/api/automation/v1/')
    assert response.status_code == 307
    assert (
        response.headers['location']
        == str(client.base_url).rstrip('/') + '/api/automation/v1'
    )


def _automation_bounded_error(client):
    """Probe automation the way a spec-compliant HTTP client would.

    A persistent (keep-alive) connection may be closed by the peer at any
    time; RFC 9112 s.9.6 requires clients to be prepared to retry an
    idempotent request on a fresh connection when this happens before a
    response is received. When a backend has just disappeared, a thin proxy
    may drop the idle reused connection to signal "reconnect" rather than
    answer on it. Retrying once on a new connection then yields the backend's
    bounded HTTP error. This does NOT accept a transport failure as success:
    the caller still asserts a bounded 502/503/504, and a second transport
    failure propagates and fails the test.
    """
    try:
        return client.get('/api/automation/ready')
    except httpx.RemoteProtocolError:
        return client.get('/api/automation/ready')


@pytest.mark.parametrize('fault', ['pause', 'stop'])
def test_enterprise_remains_ready_when_automation_is_unavailable(
    client, fault, record_property
):
    project = os.environ['REAL_STACK_PROJECT']
    with closing(docker.from_env()) as engine:
        containers = engine.containers.list(
            filters={
                'label': [
                    f'com.docker.compose.project={project}',
                    'com.docker.compose.service=automation',
                ]
            }
        )
        assert len(containers) == 1
        automation = containers[0]
        latencies = []
        try:
            getattr(automation, fault)()
            with ThreadPoolExecutor() as pool:
                failed_request = pool.submit(_automation_bounded_error, client)
                for _ in range(20):
                    started = time.monotonic()
                    assert client.get('/ready').status_code == 200
                    latencies.append(time.monotonic() - started)
                    time.sleep(0.1)
                # Record Enterprise observations before the automation outcome
                # assertion so a later failure still retains these metrics.
                record_property('enterprise_max_seconds', max(latencies))
                record_property('enterprise_successful_requests', len(latencies))
                assert failed_request.result().status_code in (502, 503, 504)
        finally:
            getattr(automation, 'unpause' if fault == 'pause' else 'start')()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if client.get('/api/automation/ready').status_code == 200:
                break
            time.sleep(0.5)
        else:
            pytest.fail('automation did not recover within 60 seconds')
