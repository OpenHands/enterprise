"""Docker-owned, database-free fixtures. Run with --confcutdir as documented."""

import ipaddress
import shutil
import ssl
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import docker
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

HERE = Path(__file__).parent
CANDIDATES = {
    'caddy': (
        'caddy@sha256:de23def33b17fb5d1290b0f6c2add1d70780e52341896c00a4c8a2a2fe9d355e',
        'Caddyfile',
        '/etc/caddy',
        None,
    ),
    'nginx': (
        'nginx@sha256:ef8676b33d681f272ba429b27658bdd7e640963279714c96bddf1dc76307f7b6',
        'nginx.conf',
        '/etc/nginx',
        None,
    ),
    'haproxy': (
        'haproxy@sha256:52c5921e1619f39cbd5b25e1b4b5847667917f39745056cf004d9c263fbf11b9',
        'haproxy.cfg',
        '/usr/local/etc/haproxy',
        ['haproxy', '-W', '-db', '-f', '/usr/local/etc/haproxy/haproxy.cfg'],
    ),
}


def certificate(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'proxy.test')])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    (directory / 'cert.pem').write_bytes(pem)
    (directory / 'key.pem').write_bytes(private)
    (directory / 'bundle.pem').write_bytes(pem + private)
    # Disposable test keys must be readable by each image's unprivileged worker.
    # The containing pytest temporary directory is private to the local user.
    return ssl.create_default_context(cafile=str(directory / 'cert.pem'))


class Proxy:
    def __init__(self, candidate, container, automation, tls, config):
        self.candidate = candidate
        self.container = container
        self.automation = automation
        self.tls = tls
        self.config = config
        self.generation = 'initial'
        self.refresh_client()

    def refresh_client(self):
        if hasattr(self, 'client'):
            self.client.close()
        self.container.reload()
        port = self.container.attrs['NetworkSettings']['Ports']['8443/tcp'][0][
            'HostPort'
        ]
        self.client = httpx.Client(
            base_url=f'https://127.0.0.1:{port}',
            verify=self.tls,
            timeout=12,
            trust_env=False,
        )

    def wait_ready(self, automation=True):
        deadline = time.monotonic() + 25
        paths = ['/echo', '/api/automation/echo'] if automation else ['/echo']
        while time.monotonic() < deadline:
            try:
                if all(
                    self.client.get(path, timeout=1).status_code == 200
                    for path in paths
                ):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        pytest.fail(
            f'{self.candidate} failed readiness: {self.container.logs(tail=20).decode()}'
        )

    def reload(self):
        generation = uuid.uuid4().hex
        self.config.write_text(
            self.config.read_text().replace(self.generation, generation)
        )
        self.generation = generation
        if self.candidate == 'haproxy':
            result = self.container.exec_run(
                ['haproxy', '-c', '-f', '/usr/local/etc/haproxy/haproxy.cfg']
            )
            assert result.exit_code == 0, result.output.decode()
            self.container.kill(signal='USR2')
        else:
            command = (
                ['caddy', 'reload', '--config', '/etc/caddy/Caddyfile']
                if self.candidate == 'caddy'
                else ['nginx', '-s', 'reload']
            )
            result = self.container.exec_run(command)
            assert result.exit_code == 0, result.output.decode()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with httpx.Client(verify=self.tls, trust_env=False, timeout=2) as client:
                response = client.get(self.client.base_url.join('/echo'))
            if response.headers.get('x-proxy-generation') == self.generation:
                return
            time.sleep(0.1)
        pytest.fail('Reload did not activate the new response header')


@pytest.fixture(scope='session')
def docker_client():
    client = docker.from_env(timeout=120)
    client.ping()  # Missing Docker is a failure, never a silently skipped comparison.
    yield client
    client.close()


@pytest.fixture(scope='session')
def fixture_image(docker_client):
    image, _ = docker_client.images.build(path=str(HERE), rm=True)
    return image.id


@pytest.fixture(scope='module', params=list(CANDIDATES))
def proxy(request, docker_client, fixture_image, tmp_path_factory):
    name = f'oh-proxy-test-{uuid.uuid4().hex[:12]}'
    directory = tmp_path_factory.mktemp(name)
    certs = directory / 'certs'
    certs.mkdir()
    tls = certificate(certs)
    config = directory / 'config'
    config.mkdir()
    image, filename, target, command = CANDIDATES[request.param]
    shutil.copy(HERE / 'configs' / filename, config / filename)
    network = docker_client.networks.create(name, labels={'openhands.proxy-test': name})
    containers = []
    rig = None
    try:
        backends = {}
        for role in ('enterprise', 'automation'):
            container = docker_client.containers.create(
                fixture_image,
                name=f'{name}-{role}',
                environment={'FIXTURE_ROLE': role},
                labels={'openhands.proxy-test': name},
                network=network.name,
            )
            containers.append(container)
            network.disconnect(container)
            network.connect(container, aliases=[role])
            container.start()
            backends[role] = container
        try:
            docker_client.images.get(image)
        except docker.errors.ImageNotFound:
            docker_client.images.pull(image)
        container = docker_client.containers.create(
            image,
            command=command,
            name=f'{name}-proxy',
            network=network.name,
            ports={'8443/tcp': ('127.0.0.1', None)},
            volumes={
                str(certs): {'bind': '/certs', 'mode': 'ro'},
                str(config): {'bind': target, 'mode': 'ro'},
            },
            labels={'openhands.proxy-test': name},
        )
        containers.append(container)
        container.start()
        rig = Proxy(
            request.param, container, backends['automation'], tls, config / filename
        )
        rig.wait_ready()
        yield rig
    finally:
        if rig:
            rig.client.close()
        for container in reversed(containers):
            container.remove(force=True, v=True)
        network.remove()
