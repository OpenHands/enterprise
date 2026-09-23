"""Installation lifecycle checks against Compose-managed transport fixtures."""

import socket
import time

import httpx
from websockets.sync.client import connect


def test_compose_starts_without_automation_and_publishes_only_proxy(compose_proxy):
    rig = compose_proxy
    assert rig.client.get('/echo').json()['role'] == 'enterprise'
    assert rig.client.get('/api/automation/echo').status_code in (502, 503, 504)
    rig.compose('up', '-d', 'automation')
    rig.automation = rig.service('automation')
    rig.wait_ready()
    for service in ('enterprise', 'automation'):
        assert not rig.service(service).attrs['HostConfig']['PortBindings']
    bindings = rig.container.attrs['HostConfig']['PortBindings']['8443/tcp']
    assert bindings[0]['HostIp'] == '127.0.0.1'


def test_replacement_changes_ip_and_recovers_without_proxy_restart(
    compose_proxy, docker_client, fixture_image, record_property
):
    rig = compose_proxy
    rig.compose('up', '-d', 'automation')
    rig.automation = rig.service('automation')
    rig.wait_ready()
    original_proxy = rig.container.id
    original_started_at = rig.container.attrs['State']['StartedAt']
    old = rig.automation.attrs['NetworkSettings']['Networks'][rig.network.name][
        'IPAddress'
    ]
    rig.compose('rm', '-s', '-f', 'automation')
    holder = docker_client.containers.create(
        fixture_image, command=['sleep', '120'], network=rig.network.name
    )
    try:
        rig.network.disconnect(holder)
        rig.network.connect(holder, ipv4_address=old)
        holder.start()
        started = time.monotonic()
        rig.compose('up', '-d', 'automation')
        rig.automation = rig.service('automation')
        new = rig.automation.attrs['NetworkSettings']['Networks'][rig.network.name][
            'IPAddress'
        ]
        assert new != old
        rig.wait_ready()
        elapsed = time.monotonic() - started
        assert elapsed < 10
        current_proxy = rig.service('proxy')
        assert current_proxy.id == original_proxy
        assert current_proxy.attrs['State']['StartedAt'] == original_started_at
        record_property('replacement_seconds_including_compose', round(elapsed, 3))
        record_property('old_ip', old)
        record_property('new_ip', new)
        assert rig.client.get('/api/automation/echo').json()['role'] == 'automation'
    finally:
        holder.remove(force=True)


def test_bad_config_is_rejected_and_previous_config_keeps_serving(compose_proxy):
    rig = compose_proxy
    original = rig.config.read_text()
    try:
        rig.write_config(original + '\ninvalid_proxy_directive_for_test {\n')
        assert rig.validate().exit_code != 0
        assert rig.client.get('/echo').headers['x-proxy-generation'] == rig.generation
    finally:
        rig.write_config(original)
    assert rig.validate().exit_code == 0
    rig.reload()
    rig.refresh_client()
    assert rig.client.get('/echo').headers['x-proxy-generation'] == rig.generation


def peer_certificate(rig, context):
    url = rig.client.base_url
    with socket.create_connection((url.host, url.port), timeout=3) as sock:
        with context.wrap_socket(sock, server_hostname=url.host) as tls:
            return tls.getpeercert(binary_form=True)


def test_certificate_rotation_keeps_websocket_and_serves_new_certificate(
    compose_proxy, record_property
):
    rig = compose_proxy
    old_certificate = peer_certificate(rig, rig.tls)
    url = str(rig.client.base_url).replace('https:', 'wss:').rstrip('/') + '/ws'
    with connect(url, ssl=rig.tls, proxy=None, open_timeout=5) as websocket:
        websocket.send('before rotation')
        assert websocket.recv(timeout=3) == 'enterprise:before rotation'
        started = time.monotonic()
        new_context = rig.rotate_certificate()
        rig.tls = new_context
        rig.reload()
        deadline = time.monotonic() + 5
        consecutive = 0
        old_observations = 0
        while time.monotonic() < deadline and consecutive < 5:
            if peer_certificate(rig, new_context) != old_certificate:
                consecutive += 1
            else:
                old_observations += 1
                consecutive = 0
            time.sleep(0.1)
        assert consecutive == 5, 'New certificate did not become stable within 5s'
        record_property(
            'certificate_rotation_seconds', round(time.monotonic() - started, 3)
        )
        record_property('old_certificate_observations_after_reload', old_observations)
        websocket.send('after rotation')
        assert websocket.recv(timeout=3) == 'enterprise:after rotation'
        with httpx.Client(verify=new_context, trust_env=False, timeout=3) as client:
            assert client.get(rig.client.base_url.join('/echo')).status_code == 200
    rig.refresh_client()


def test_compose_down_and_up_uses_existing_configuration(compose_proxy):
    rig = compose_proxy
    old_certificate = peer_certificate(rig, rig.tls)
    rig.compose('down', '--volumes', '--remove-orphans')
    rig.compose('up', '-d')
    rig.container = rig.service('proxy')
    rig.automation = rig.service('automation')
    rig.refresh_client()
    rig.wait_ready()
    assert peer_certificate(rig, rig.tls) == old_certificate
    assert rig.client.get('/api/automation/echo').json()['role'] == 'automation'
