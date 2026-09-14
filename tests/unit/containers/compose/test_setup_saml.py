"""Exercise local signing credential lifecycle and Compose trust boundaries."""

import base64
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from pydantic import BaseModel, ConfigDict, Field, JsonValue

ROOT = Path(__file__).resolve().parents[4]
HELPER = ROOT / 'containers/compose/setup_saml.py'
pytestmark = pytest.mark.skipif(
    shutil.which('openssl') is None,
    reason='OpenSSL CLI is required for the local signing credential tests',
)


class ComposeResponse(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)


class ComposeBind(ComposeResponse):
    create_host_path: bool = True


class ComposeMount(ComposeResponse):
    source: str
    target: str
    read_only: bool = False
    bind: ComposeBind = Field(default_factory=ComposeBind)


class ComposePort(ComposeResponse):
    host_ip: str
    published: str


class ComposeService(ComposeResponse):
    environment: dict[str, str] = Field(default_factory=dict)
    volumes: list[ComposeMount] = Field(default_factory=list)
    networks: dict[str, JsonValue] = Field(default_factory=dict)
    ports: list[ComposePort] = Field(default_factory=list)


class ComposeConfig(ComposeResponse):
    services: dict[str, ComposeService]
    volumes: dict[str, JsonValue]


def run_setup(destination: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER), '--output-dir', str(destination)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def credentials(tmp_path: Path) -> Path:
    destination = tmp_path / 'private signing credentials'
    result = run_setup(destination, tmp_path)
    assert result.returncode == 0, result.stderr
    return destination


@pytest.fixture
def compose_cli() -> None:
    try:
        result = subprocess.run(
            ['docker', 'compose', 'version'], capture_output=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip('Docker Compose CLI is required for the configuration test')
    if result.returncode != 0:
        pytest.skip('Docker Compose CLI is required for the configuration test')


def contents(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in directory.iterdir()}


def test_generated_credentials_match_and_do_not_modify_application_environment(
    tmp_path: Path,
) -> None:
    app_environment = tmp_path / '.env'
    app_environment.write_text('JWT_SECRET=keep-me\nOPENHANDS_PORT=13000\n')
    destination = tmp_path / 'credentials with spaces'
    result = run_setup(destination, tmp_path)
    assert result.returncode == 0, result.stderr
    assert app_environment.read_text() == 'JWT_SECRET=keep-me\nOPENHANDS_PORT=13000\n'
    files = contents(destination)
    assert set(files) == {'idp.crt', 'idp.key', 'mocksaml.env'}
    environment = dict(
        line.split('=', 1) for line in files['mocksaml.env'].decode().splitlines()
    )
    certificate = x509.load_pem_x509_certificate(
        base64.b64decode(environment['PUBLIC_KEY'], validate=True)
    )
    private_key = serialization.load_pem_private_key(
        base64.b64decode(environment['PRIVATE_KEY'], validate=True), password=None
    )
    assert base64.b64decode(environment['PUBLIC_KEY']) == files['idp.crt']
    assert base64.b64decode(environment['PRIVATE_KEY']) == files['idp.key']
    assert certificate.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ) == private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    for path in (destination, destination / 'idp.key', destination / 'mocksaml.env'):
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    assert environment['PRIVATE_KEY'] not in result.stdout + result.stderr


def test_setup_preserves_existing_credentials(
    credentials: Path, tmp_path: Path
) -> None:
    original = contents(credentials)
    result = run_setup(credentials, tmp_path)
    assert result.returncode == 0, result.stderr
    assert contents(credentials) == original
    assert 'Kept existing' in result.stdout


def test_setup_refuses_mismatched_environment_without_replacing_files(
    credentials: Path, tmp_path: Path
) -> None:
    (credentials / 'mocksaml.env').write_text('PRIVATE_KEY=invalid\n')
    original = contents(credentials)
    result = run_setup(credentials, tmp_path)
    assert result.returncode == 1
    assert contents(credentials) == original


def test_setup_refuses_partial_existing_directory(tmp_path: Path) -> None:
    destination = tmp_path / 'incomplete'
    destination.mkdir()
    (destination / 'idp.crt').write_text('preserve this file')
    original = contents(destination)
    result = run_setup(destination, tmp_path)
    assert result.returncode == 1
    assert contents(destination) == original


def test_failed_first_setup_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / 'new-credentials'
    with monkeypatch.context() as context:
        context.setenv('PATH', '')
        result = run_setup(destination, tmp_path)
    assert result.returncode == 1
    assert not destination.exists()
    result = run_setup(destination, tmp_path)
    assert result.returncode == 0, result.stderr
    assert set(contents(destination)) == {'idp.crt', 'idp.key', 'mocksaml.env'}


def test_setup_refuses_symlinked_credentials(credentials: Path, tmp_path: Path) -> None:
    alias = tmp_path / 'alias'
    alias.symlink_to(credentials, target_is_directory=True)
    original = contents(credentials)
    result = run_setup(alias, tmp_path)
    assert result.returncode == 1
    assert contents(credentials) == original


@pytest.mark.usefixtures('compose_cli')
def test_compose_overlay_preserves_base_settings_and_isolates_idp_credentials(
    credentials: Path, tmp_path: Path
) -> None:
    for filename in ('docker-compose.yml', 'docker-compose.saml.yml'):
        shutil.copyfile(ROOT / filename, tmp_path / filename)
    credentials.rename(tmp_path / '.mocksaml')
    (tmp_path / '.env').write_text(
        'COMPOSE_PROJECT_NAME=saml-config-test\nOPENHANDS_PORT=13000\n'
        'MOCKSAML_PORT=14000\nDOCKER_SOCKET_PATH=/tmp/custom-docker.sock\n'
        'DB_PASSWORD=database-test-password\nJWT_SECRET=app-test-jwt\n'
        'SUPERADMIN_EMAIL=admin@example.test\nSUPERADMIN_PASSWORD=admin-test-password\n'
    )

    def config(overlay: bool) -> ComposeConfig:
        command = ['docker', 'compose', '-f', 'docker-compose.yml']
        if overlay:
            command += ['-f', 'docker-compose.saml.yml']
        result = subprocess.run(
            [*command, 'config', '--format', 'json'],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return ComposeConfig.model_validate_json(result.stdout)

    base = config(False)
    combined = config(True)
    assert 'mocksaml' not in base.services
    assert base.volumes == combined.volumes
    for service in ('init', 'openhands'):
        original = base.services[service]
        merged = combined.services[service]
        assert original.environment.items() <= merged.environment.items()
        assert merged.environment['NATIVE_SAML_ENABLED'] == 'true'
        assert merged.environment['NATIVE_AUTH_APP_ORIGIN'] == 'http://localhost:13000'
        assert merged.environment['NATIVE_SAML_IDP_SSO_URL'] == (
            'http://localhost:14000/api/saml/sso'
        )
        certificate_mount = [
            mount
            for mount in merged.volumes
            if mount.target == '/run/secrets/mocksaml/idp.crt'
        ]
        assert len(certificate_mount) == 1
        assert certificate_mount[0].read_only is True
        assert certificate_mount[0].bind.create_host_path is False
        assert 'PRIVATE_KEY' not in merged.environment
        assert all('idp.key' not in mount.source for mount in merged.volumes)
    mocksaml = combined.services['mocksaml']
    assert set(mocksaml.environment) == {
        'APP_URL',
        'ENTITY_ID',
        'PUBLIC_KEY',
        'PRIVATE_KEY',
    }
    assert mocksaml.environment['APP_URL'] == 'http://localhost:14000'
    assert set(mocksaml.networks) == {'saml'}
    assert mocksaml.ports[0].host_ip == '127.0.0.1'
    assert mocksaml.ports[0].published == '14000'
