"""Trust validation and repeatability of the optional authentik deployment setup."""

import json
import stat
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from containers.compose.gcp import authentik_setup as setup


class FakeAPI(setup.AuthentikAPI):
    def __init__(self, responses: list[setup.JSONObject]) -> None:
        super().__init__('https://auth.example.com', 'private-token')
        self.responses = responses
        self.failures: dict[int, ValueError] = {}
        self.calls: list[
            tuple[str, str, setup.JSONObject | setup.ProviderSettings | None]
        ] = []
        self.lookups: list[setup.JSONObject | None] | None = None

    def request(
        self,
        method: str,
        path: str,
        body: setup.JSONObject | setup.ProviderSettings | None = None,
    ) -> setup.JSONObject:
        self.calls.append((method, path, body))
        failure = self.failures.get(len(self.calls))
        if failure is not None:
            raise failure
        return self.responses.pop(0)

    def lookup(self, resource: str, **filters: str | int) -> setup.JSONObject | None:
        if self.lookups is not None:
            return self.lookups.pop(0)
        return super().lookup(resource, **filters)


@dataclass
class PolicyRequest:
    debug: bool = False


def test_prepare_preserves_native_credentials_and_reuses_secrets(
    tmp_path: Path,
) -> None:
    private = tmp_path / 'private'
    private.mkdir()
    env = private / 'remote.env'
    env.write_text('SUPERADMIN_PASSWORD=existing-native-secret\nCUSTOM_OPTION=keep\n')
    credentials_file = private / 'credentials.json'
    credentials_file.write_text(json.dumps({'openhands_password': 'existing-password'}))

    setup.prepare(env, private, 'app.example.com', 'auth.example.com')
    first_credentials = credentials_file.read_text()
    first_bootstrap = (private / 'authentik-bootstrap.env').read_text()
    first_env = setup.read_env(env)
    setup.prepare(env, private, 'app.example.com', 'auth.example.com')

    assert credentials_file.read_text() == first_credentials
    assert (private / 'authentik-bootstrap.env').read_text() == first_bootstrap
    assert setup.read_env(env) == first_env
    assert first_env['SUPERADMIN_PASSWORD'] == 'existing-native-secret'
    assert first_env['CUSTOM_OPTION'] == 'keep'
    assert (
        setup.decode_object(first_credentials)['openhands_password']
        == 'existing-password'
    )
    assert len(setup.array(setup.decode_object(first_credentials)['saml_users'])) == 2
    for path in private.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_prepare_does_not_resurrect_revoked_token(tmp_path: Path) -> None:
    env = tmp_path / 'remote.env'
    setup.prepare(env, tmp_path, 'app.example.com', 'auth.example.com')
    bootstrap = tmp_path / 'authentik-bootstrap.env'
    bootstrap.write_text('# revoked\n')
    setup.update_env(env, {'NATIVE_SAML_ENABLED': 'true'})

    setup.prepare(env, tmp_path, 'app.example.com', 'auth.example.com')

    assert bootstrap.read_text() == '# revoked\n'
    assert setup.read_env(env)['NATIVE_SAML_ENABLED'] == 'true'


def test_prepare_refuses_identity_host_change(tmp_path: Path) -> None:
    env = tmp_path / 'remote.env'
    setup.prepare(env, tmp_path, 'app.example.com', 'auth.example.com')
    with pytest.raises(ValueError, match='refusing to change identity'):
        setup.prepare(env, tmp_path, 'app.example.com', 'different.example.com')


def test_private_write_refuses_symlink(tmp_path: Path) -> None:
    destination = tmp_path / 'destination'
    destination.write_text('untouched')
    link = tmp_path / 'link'
    link.symlink_to(destination)
    with pytest.raises(ValueError, match='Refusing symlink'):
        setup.private_write(link, 'overwrite')
    assert destination.read_text() == 'untouched'


def test_bootstrap_probe_does_not_expose_container_error_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(
        command: list[str], *, capture_output: bool, timeout: int
    ) -> subprocess.CompletedProcess[bytes]:
        assert capture_output is True
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, stderr=b'private-data')

    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(ValueError, match='leave the proxy stopped') as failure:
        setup.check_bootstrap(Path('.env'))
    assert 'private-data' not in str(failure.value)
    assert calls[0][2:4] == ['--env-file', '.env']


@pytest.mark.parametrize(
    'value',
    [
        'https://auth.example.com',
        'auth.example.com:9000',
        'auth..example.com',
        'auth.-example.com',
        'auth.example.com/path',
    ],
)
def test_hostname_rejects_origin_and_invalid_dns_values(value: str) -> None:
    with pytest.raises(ValueError):
        setup.hostname(value)


def test_lookup_refuses_ambiguous_objects() -> None:
    api = FakeAPI([{'results': [{'pk': 1}, {'pk': 2}]}])
    with pytest.raises(ValueError, match='Ambiguous'):
        api.lookup('core/users', username='alice')


def test_application_lookup_includes_apps_denied_to_admin_launcher() -> None:
    existing: setup.JSONObject = {'pk': 'owned-application', 'slug': 'openhands'}
    api = FakeAPI([{'results': [existing]}])

    assert api.lookup('core/applications', slug='openhands') == existing
    assert api.calls == [
        (
            'GET',
            '/api/v3/core/applications/?slug=openhands&superuser_full_list=true',
            None,
        )
    ]


def test_existing_flow_is_updated_by_slug() -> None:
    api = FakeAPI([{}])
    api.lookups = [{'pk': 'flow-uuid', 'slug': 'owned-flow'}]
    body: setup.JSONObject = {'slug': 'owned-flow', 'title': 'Updated title'}
    api.upsert('flows/instances', body, 'owned-flow', key='slug')
    assert api.calls == [('PATCH', '/api/v3/flows/instances/owned-flow/', body)]


@pytest.mark.parametrize('is_sso,slug', [(False, 'openhands'), (True, 'other-app')])
def test_reauth_cleanup_rejects_other_flows(is_sso: bool, slug: str) -> None:
    request = PolicyRequest()
    session = {'authentik/providers/saml/last_login_uid': 'previous-login'}
    results: list[bool] = []
    namespace: dict[str, object] = {
        'results': results,
        'request': request,
        'http_request': SimpleNamespace(session=session),
        'context': {'application': SimpleNamespace(slug=slug)},
        'ak_is_sso_flow': is_sso,
    }
    exec(
        'def evaluate() -> bool:\n'
        + '\n'.join('    ' + line for line in setup.REAUTH_EXPRESSION.splitlines()),
        namespace,
    )
    exec('results.append(evaluate())', namespace)
    assert results == [False]
    assert session['authentik/providers/saml/last_login_uid'] == 'previous-login'


def test_reauth_cleanup_is_repeatable_and_preserves_unrelated_session_data() -> None:
    request = PolicyRequest()
    session = {'unrelated': 'preserved'}
    results: list[bool] = []
    namespace: dict[str, object] = {
        'results': results,
        'request': request,
        'http_request': SimpleNamespace(session=session),
        'context': {'application': SimpleNamespace(slug='openhands')},
        'ak_is_sso_flow': True,
    }
    exec(
        'def evaluate() -> bool:\n'
        + '\n'.join('    ' + line for line in setup.REAUTH_EXPRESSION.splitlines()),
        namespace,
    )
    for login in ('first-login', 'second-login'):
        session['authentik/providers/saml/last_login_uid'] = login
        exec('results.append(evaluate())', namespace)
        assert results[-1] is True
        assert session == {'unrelated': 'preserved'}
        assert request.debug is True  # PolicyRequest.should_cache becomes false.


def test_initial_password_failure_is_retried_without_resetting_completed_passwords(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def password_hash(value: str) -> str:
        return 'private-verifier'

    monkeypatch.setattr(setup, 'password_hash', password_hash)
    demo: setup.DemoCredentials = {
        'username': 'alice',
        'name': 'Alice',
        'email': 'alice@example.com',
        'password': 'private-password',
    }
    user: setup.JSONObject = {
        'username': demo['username'],
        'email': demo['email'],
        'pk': 3,
        'uid': 'stable-opaque-subject',
        'path': 'users/openhands-demo',
        'is_superuser': False,
    }
    api = FakeAPI(
        [
            user,
            user,
            {},
            user,
        ]
    )
    api.lookups = [None, user, user]
    api.failures = {2: ValueError('temporary API failure')}

    with pytest.raises(ValueError, match='temporary API failure'):
        setup.provision_demo_users(api, tmp_path, [demo], 'ordinary-group')
    pending_file = tmp_path / 'authentik-pending-passwords.json'
    assert setup.pending_passwords(pending_file)['alice']['pk'] == 3

    result = setup.provision_demo_users(api, tmp_path, [demo], 'ordinary-group')
    assert result[0]['uid'] == 'stable-opaque-subject'
    assert setup.pending_passwords(pending_file) == {}

    setup.provision_demo_users(api, tmp_path, [demo], 'ordinary-group')
    password_calls = [
        call for call in api.calls if call[1].endswith('/set_password_hash/')
    ]
    assert len(password_calls) == 2


def test_provider_uses_post_signed_assertions_and_persistent_opaque_nameid() -> None:
    result = setup.provider_settings(
        'https://app.example.com', {}, 'email-mapping', 'signing-key'
    )
    assert result['acs_url'] == 'https://app.example.com/api/auth/saml/acs'
    assert result['audience'] == 'https://app.example.com/api/auth/saml/metadata'
    assert result['sp_binding'] == 'post'
    assert result['sign_assertion'] is True
    assert result['signing_kp'] == 'signing-key'
    assert result['signature_algorithm'].endswith('rsa-sha256')
    assert result['name_id_mapping'] is None
    assert result['default_name_id_policy'] == setup.PERSISTENT_NAME_ID
    assert result['property_mappings'] == ['email-mapping']


def metadata(
    certificate: str = 'Y2VydA==',
    issuer: str = 'https://auth.example.com/application/saml/openhands/metadata/',
) -> str:
    return f"""<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{issuer}">
      <IDPSSODescriptor>
        <KeyDescriptor use="signing"><KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#"><X509Data><X509Certificate>{certificate}</X509Certificate></X509Data></KeyInfo></KeyDescriptor>
        <NameIDFormat>{setup.PERSISTENT_NAME_ID}</NameIDFormat>
        <SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://auth.example.com/application/saml/openhands/"/>
      </IDPSSODescriptor>
    </EntityDescriptor>"""


def test_metadata_rejects_certificate_mismatch() -> None:
    with pytest.raises(ValueError, match='does not match'):
        setup.validate_metadata(
            metadata(),
            '-----BEGIN CERTIFICATE-----\nb3RoZXI=\n-----END CERTIFICATE-----\n',
            'https://auth.example.com',
        )


def test_metadata_rejects_unexpected_issuer() -> None:
    with pytest.raises(ValueError, match='issuer differs'):
        setup.validate_metadata(
            metadata(issuer='https://untrusted.example.com'),
            'Y2VydA==',
            'https://auth.example.com',
        )


def test_metadata_rejects_dtd() -> None:
    with pytest.raises(ValueError, match='Unexpected XML'):
        setup.validate_metadata(
            '<!DOCTYPE root>' + metadata(), 'Y2VydA==', 'https://auth.example.com'
        )


def test_metadata_accepts_matching_operator_trust() -> None:
    fingerprint = setup.validate_metadata(
        metadata(),
        '-----BEGIN CERTIFICATE-----\nY2VydA==\n-----END CERTIFICATE-----\n',
        'https://auth.example.com',
    )
    assert len(fingerprint) == 64


@pytest.mark.parametrize(
    'document',
    [
        '{"authentik_password": ["private-value"]}',
        '{"saml_users": [{"username": "private-value", "password": []}]}',
        '["private-value"]',
        'not-json-private-value',
    ],
)
def test_prepare_rejects_invalid_credentials_without_printing_or_replacing_them(
    tmp_path: Path, document: str
) -> None:
    credentials = tmp_path / 'credentials.json'
    credentials.write_text(document)
    with pytest.raises((ValueError, KeyError)) as failure:
        setup.prepare(
            tmp_path / 'remote.env', tmp_path, 'app.example.com', 'auth.example.com'
        )
    assert 'private-value' not in str(failure.value)
    assert credentials.read_text() == document
    assert not (tmp_path / 'authentik-bootstrap.env').exists()


@dataclass
class HTTPFixture:
    api: setup.AuthentikAPI
    paths: list[str]


@pytest.fixture
def http_api() -> Iterator[HTTPFixture]:
    paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return None

        def do_GET(self) -> None:
            paths.append(self.path)
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/private-target')
                self.end_headers()
                self.wfile.write(b'private-token')
                return
            self.send_response(200)
            self.end_headers()
            content = {
                '/object': b'{"results": [{"pk": 5}]}',
                '/invalid-json': b'{private-token',
                '/invalid-object': b'["private-token"]',
                '/invalid-text': b'\xffprivate-token',
                '/text': b'plain certificate text',
                '/empty': b'',
            }
            self.wfile.write(content.get(self.path, b'private-token'))

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield HTTPFixture(
                setup.AuthentikAPI(
                    f'http://127.0.0.1:{server.server_port}', 'private-token'
                ),
                paths,
            )
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_administrator_http_requests_never_follow_redirects(
    http_api: HTTPFixture,
) -> None:
    with pytest.raises(ValueError, match='HTTP 302') as failure:
        http_api.api.request('GET', '/redirect')
    assert 'private-token' not in str(failure.value)
    assert http_api.paths == ['/redirect']


@pytest.mark.parametrize('path', ['/invalid-json', '/invalid-object', '/invalid-text'])
def test_http_boundary_rejects_private_invalid_payloads(
    http_api: HTTPFixture, path: str
) -> None:
    with pytest.raises(ValueError) as failure:
        http_api.api.request('GET', path)
    assert 'private-token' not in str(failure.value)


def test_http_json_text_and_empty_responses_keep_distinct_contracts(
    http_api: HTTPFixture,
) -> None:
    assert http_api.api.request('GET', '/object') == {'results': [{'pk': 5}]}
    assert http_api.api.request_text('GET', '/text') == 'plain certificate text'
    assert http_api.api.request('GET', '/empty') == {}
