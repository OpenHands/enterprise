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
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

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
    # Keep a stable test CA across leaf rotations, as normal certificate renewal
    # does. Its private key stays outside the directory mounted into the proxy.
    ca_path = directory.parent / 'test-ca.pem'
    ca_key_path = directory.parent / 'test-ca-key.pem'
    now = datetime.now(UTC)
    if not ca_path.exists():
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_subject = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, 'Proxy Test CA')]
        )
        ca = (
            x509.CertificateBuilder()
            .subject_name(ca_subject)
            .issuer_name(ca_subject)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        ca_key_path.write_bytes(
            ca_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        ca_key_path.chmod(0o600)
    ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'proxy.test')])
        )
        .issuer_name(ca.subject)
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
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    # Atomic replacement also changes inode: nginx can inherit unchanged SSL
    # objects across reloads when both file inode and modification time match.
    for name, data in [
        ('cert.pem', pem),
        ('key.pem', private),
        ('bundle.pem', pem + private),
    ]:
        temporary = directory / (name + '.new')
        temporary.write_bytes(data)
        temporary.replace(directory / name)
    return ssl.create_default_context(cafile=str(ca_path))


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

    def write_config(self, text):
        temporary = self.config.with_suffix(self.config.suffix + '.pending')
        temporary.write_text(text)
        temporary.replace(self.config)
        # Desktop VM bind mounts can briefly expose new bytes with stale file
        # size metadata. Confirm both before asking a parser to load the file.
        target = CANDIDATES[self.candidate][2] + '/' + self.config.name
        deadline = time.monotonic() + 5
        expected = text.encode()
        while time.monotonic() < deadline:
            contents = self.container.exec_run(['cat', target])
            size = self.container.exec_run(['stat', '-c', '%s', target])
            if (
                contents.exit_code == size.exit_code == 0
                and contents.output == expected
                and size.output.strip() == str(len(expected)).encode()
            ):
                return
            time.sleep(0.1)
        pytest.fail('Config bind mount did not expose the complete new file within 5s')

    def validate(self):
        commands = {
            'caddy': ['caddy', 'validate', '--config', '/etc/caddy/Caddyfile'],
            'nginx': ['nginx', '-t'],
            'haproxy': ['haproxy', '-c', '-f', '/usr/local/etc/haproxy/haproxy.cfg'],
        }
        return self.container.exec_run(commands[self.candidate])

    def reload(self):
        generation = uuid.uuid4().hex
        self.write_config(self.config.read_text().replace(self.generation, generation))
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
            try:
                with httpx.Client(
                    verify=self.tls, trust_env=False, timeout=2
                ) as client:
                    response = client.get(self.client.base_url.join('/echo'))
                if response.headers.get('x-proxy-generation') == self.generation:
                    return
            except httpx.ConnectError:
                # Rotation is asynchronous: keep verifying TLS while waiting for
                # the new worker/certificate, never disable verification.
                pass
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


@pytest.fixture(params=list(CANDIDATES))
def compose_proxy(request, docker_client, fixture_image, tmp_path, record_property):
    """A fresh project per test; start proxy before either upstream exists."""
    import json
    import os
    import subprocess

    name = f'oh-compose-test-{uuid.uuid4().hex[:12]}'
    certs = tmp_path / 'certs'
    certs.mkdir()
    tls = certificate(certs)
    config = tmp_path / 'config'
    config.mkdir()
    image, filename, target, command = CANDIDATES[request.param]
    shutil.copy(HERE / 'configs' / filename, config / filename)
    manifest = tmp_path / 'compose.yaml'
    shutil.copy(HERE / 'compose.yaml', manifest)
    override = tmp_path / 'override.json'
    override.write_text(json.dumps({'services': {'proxy': {'command': command}}}))
    environment = os.environ | {
        'FIXTURE_IMAGE': fixture_image,
        'PROXY_IMAGE': image,
        'PROXY_CERT_DIR': str(certs),
        'PROXY_CONFIG_DIR': str(config),
        'PROXY_CONFIG_TARGET': target,
    }

    def compose(*args):
        return subprocess.run(
            [
                'docker',
                'compose',
                '--project-name',
                name,
                '-f',
                str(manifest),
                '-f',
                str(override),
                *args,
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout

    def service(role):
        return docker_client.containers.get(compose('ps', '-q', role).strip())

    rig = None
    started = time.monotonic()
    try:
        compose('config', '--quiet')
        compose('up', '-d', '--no-deps', 'proxy')
        compose('up', '-d', 'enterprise')
        rig = Proxy(request.param, service('proxy'), None, tls, config / filename)
        rig.install_started_at = started

        def swap_candidate(candidate):
            image, filename, target, command = CANDIDATES[candidate]
            # Freeze the originally published port for every candidate/rollback.
            port = rig.client.base_url.port
            manifest.write_text(
                manifest.read_text().replace(
                    '127.0.0.1::8443', f'127.0.0.1:{port}:8443'
                )
            )
            shutil.copy(HERE / 'configs' / filename, config / filename)
            environment.update(PROXY_IMAGE=image, PROXY_CONFIG_TARGET=target)
            override.write_text(
                json.dumps({'services': {'proxy': {'command': command}}})
            )
            compose('config', '--quiet')
            compose('up', '-d', '--no-deps', 'proxy')
            rig.candidate = candidate
            rig.config = config / filename
            rig.generation = 'initial'
            rig.container = service('proxy')
            rig.refresh_client()
            rig.wait_ready()

        rig.swap_candidate = swap_candidate
        rig.compose = compose
        rig.service = service
        rig.network = docker_client.networks.get(f'{name}_default')
        rig.rotate_certificate = lambda: certificate(certs)
        rig.wait_ready(automation=False)
        record_property(
            'startup_seconds_cached_images', round(time.monotonic() - started, 3)
        )
        yield rig
    finally:
        if rig:
            rig.client.close()
        if rig:
            record_property('proxy_log_tail', rig.container.logs(tail=12).decode())
        compose('down', '--volumes', '--remove-orphans')
