"""Prepare private credentials and configure the demonstration authentik SAML IdP."""

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.message import Message
from http.client import HTTPResponse
from pathlib import Path
from typing import IO, Literal, NotRequired, TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

PERSISTENT_NAME_ID = 'urn:oasis:names:tc:SAML:2.0:nameid-format:persistent'
GROUP_NAME = 'OpenHands Demo Users'
PROVIDER_NAME = 'OpenHands Enterprise'
CERTIFICATE_NAME = 'OpenHands Demo SAML'
MAPPING_NAME = 'OpenHands Email'
AUTHORIZATION_FLOW = 'openhands-provider-authorization'
REAUTH_POLICY = 'OpenHands SAML reauthentication cleanup'
REAUTH_EXPRESSION = """# authentik 2026.8.2: discard the completed ForceAuthn marker.
# FlowPlanner calls this only after SAMLSSOView.check_force_authn succeeds.
# A cached policy result would skip this session mutation on the next request.
request.debug = True
application = context.get("application")
if not ak_is_sso_flow or application is None or application.slug != "openhands":
    return False
http_request.session.pop("authentik/providers/saml/last_login_uid", None)
return True
"""
BOOTSTRAP_TOKEN_ID = 'authentik-bootstrap-token'
NS = {
    'md': 'urn:oasis:names:tc:SAML:2.0:metadata',
    'ds': 'http://www.w3.org/2000/09/xmldsig#',
}


# JSON is untrusted at the file/HTTP boundary. Normalize it before reading fields;
# validation errors deliberately contain no values from the private input.
type JSONValue = (
    str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]
)
type JSONObject = dict[str, JSONValue]
type Identifier = str | int


def json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        items: Sequence[object] = value
        return [json_value(item) for item in items]
    if isinstance(value, dict):
        entries: Mapping[object, object] = value
        return {string(key): json_value(item) for key, item in entries.items()}
    raise ValueError('Expected a JSON value')


def string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError('Expected a JSON string')
    return value


def identifier(value: JSONValue) -> Identifier:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('Expected an object identifier')
    return value


def boolean(value: JSONValue) -> bool:
    if not isinstance(value, bool):
        raise ValueError('Expected a JSON boolean')
    return value


def object_value(value: JSONValue) -> JSONObject:
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def array(value: JSONValue) -> list[JSONValue]:
    if not isinstance(value, list):
        raise ValueError('Expected a JSON array')
    return value


def decode_object(content: str | bytes) -> JSONObject:
    try:
        decoded: object = json.loads(content)
    except (ValueError, UnicodeError):
        raise ValueError('Invalid JSON document') from None
    return object_value(json_value(decoded))


class DemoCredentials(TypedDict):
    username: str
    name: str
    email: str
    password: str


def demo_credentials(value: JSONValue) -> DemoCredentials:
    fields = object_value(value)
    return DemoCredentials(
        username=string(fields['username']),
        name=string(fields['name']),
        email=string(fields['email']),
        password=string(fields['password']),
    )


class PendingPassword(TypedDict):
    email: str
    pk: NotRequired[Identifier]
    uid: NotRequired[str]


def pending_passwords(path: Path) -> dict[str, PendingPassword]:
    document = decode_object(path.read_text()) if path.exists() else {}
    result: dict[str, PendingPassword] = {}
    for username, value in document.items():
        fields = object_value(value)
        operation = PendingPassword(email=string(fields['email']))
        if 'pk' in fields or 'uid' in fields:
            operation['pk'] = identifier(fields['pk'])
            operation['uid'] = string(fields['uid'])
        result[username] = operation
    return result


@dataclass(frozen=True)
class UserResponse:
    pk: Identifier
    uid: str
    username: str
    email: str
    path: str
    is_superuser: bool

    @classmethod
    def parse(cls, fields: JSONObject) -> 'UserResponse':
        return cls(
            pk=identifier(fields['pk']),
            uid=string(fields['uid']),
            username=string(fields['username']),
            email=string(fields['email']),
            path=string(fields['path']),
            is_superuser=boolean(fields['is_superuser']),
        )


class UserSummary(TypedDict):
    username: str
    id: Identifier
    uid: str
    is_superuser: bool


class FlowFields(TypedDict, total=False):
    authentication_flow: str
    invalidation_flow: str
    authorization_flow: str


class ProviderSettings(FlowFields):
    name: str
    acs_url: str
    audience: str
    issuer_override: str
    sp_binding: Literal['post']
    property_mappings: list[str]
    name_id_mapping: None
    default_name_id_policy: str
    signing_kp: str
    sign_assertion: bool
    sign_response: bool
    signature_algorithm: str
    digest_algorithm: str
    verification_kp: None
    encryption_kp: None
    assertion_valid_not_before: str
    assertion_valid_not_on_or_after: str
    session_valid_not_on_or_after: str
    default_relay_state: str
    sls_url: str


class Arguments(argparse.Namespace):
    action: Literal['prepare', 'check-bootstrap', 'provision', 'finalize']
    env_file: Path
    private_dir: Path
    app_host: str | None
    identity_host: str | None
    token_file: Path | None


def private_write(path: Path, content: str) -> None:
    """Replace a private file atomically without a permissive creation window."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError(f'Refusing symlink: {path.name}')
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def read_env(path: Path) -> dict[str, str]:
    """Read our generated literal environment values without shell evaluation."""
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key] = value.strip().strip("'")
    return values


def update_env(path: Path, updates: dict[str, str]) -> None:
    """Preserve unrelated configuration and replace only the named settings."""
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [line for line in lines if line.split('=', 1)[0] not in updates]
    lines.extend(f'{key}={value}' for key, value in updates.items())
    private_write(path, '\n'.join(lines) + '\n')


def password_hash(password: str) -> str:
    """Encode a salted Django PBKDF2 verifier without exposing a plaintext argument."""
    iterations = 1_000_000
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), iterations)
    return f'pbkdf2_sha256${iterations}${salt}${base64.b64encode(digest).decode()}'


def hostname(value: str) -> str:
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', value):
        raise ValueError('Expected a lowercase DNS hostname without a scheme or port')
    if any(
        not part or part.startswith('-') or part.endswith('-')
        for part in value.split('.')
    ):
        raise ValueError('Invalid DNS hostname')
    return value


def prepare(
    env_file: Path, private_dir: Path, app_host: str, identity_host: str
) -> None:
    app_host, identity_host = hostname(app_host), hostname(identity_host)
    private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_dir.chmod(0o700)
    credentials_file = private_dir / 'credentials.json'
    credentials = (
        decode_object(credentials_file.read_text()) if credentials_file.exists() else {}
    )
    expected = {
        'authentik_url': f'https://{identity_host}',
        'authentik_username': 'akadmin',
        'authentik_email': f'authentik-admin@{app_host}',
    }
    for key, value in expected.items():
        if key in credentials and credentials[key] != value:
            raise ValueError(f'Existing {key} differs; refusing to change identity')
        credentials[key] = value
    credentials.setdefault('authentik_password', secrets.token_urlsafe(24))
    if 'saml_users' not in credentials:
        credentials['saml_users'] = [
            {
                'username': name,
                'name': f'{name.title()} Demo',
                'email': f'{name}@{app_host}',
                'password': secrets.token_urlsafe(24),
            }
            for name in ('alice', 'bob')
        ]
    # Validate existing credentials before persisting or using a password.
    string(credentials['authentik_password'])
    for demo in array(credentials['saml_users']):
        demo_credentials(demo)
    private_write(credentials_file, json.dumps(credentials, indent=2) + '\n')
    env = read_env(env_file)
    update_env(
        env_file,
        {
            'AUTHENTIK_HOST': identity_host,
            'AUTHENTIK_SECRET_KEY': env.get(
                'AUTHENTIK_SECRET_KEY', secrets.token_hex(48)
            ),
            'AUTHENTIK_DB_PASSWORD': env.get(
                'AUTHENTIK_DB_PASSWORD', secrets.token_hex(24)
            ),
            'NATIVE_SAML_ENABLED': env.get('NATIVE_SAML_ENABLED', 'false'),
        },
    )
    bootstrap = private_dir / 'authentik-bootstrap.env'
    # Never resurrect a revoked token when preparation is repeated.
    if not bootstrap.exists():
        verifier = password_hash(string(credentials['authentik_password']))
        private_write(
            bootstrap,
            f"AUTHENTIK_BOOTSTRAP_PASSWORD_HASH='{verifier}'\n"
            f'AUTHENTIK_BOOTSTRAP_EMAIL={credentials["authentik_email"]}\n'
            f'AUTHENTIK_BOOTSTRAP_TOKEN={secrets.token_hex(32)}\n',
        )
    certificate = private_dir / 'authentik-idp-certificate.pem'
    if not certificate.exists():
        private_write(certificate, '')
    print(json.dumps({'prepared': True, 'credentials_file': str(credentials_file)}))


def check_bootstrap(env_file: Path) -> None:
    """Verify the worker's private bootstrap token before publishing the IdP."""
    probe = """import json, os
from collections.abc import Mapping, Sequence
from email.message import Message
from http.client import HTTPResponse
from typing import IO
from urllib.request import HTTPRedirectHandler, Request, build_opener

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: IO[bytes], code: int,
                         msg: str, headers: Message, newurl: str) -> None:
        return None

request = Request(
    "http://authentik-server:9000/api/v3/core/users/?username=akadmin",
    headers={
        "Authorization": "Bearer " + os.environ["AUTHENTIK_BOOTSTRAP_TOKEN"],
        "Host": os.environ["AUTHENTIK_WEB__BASE_URL"].removeprefix("https://"),
    },
)
response: HTTPResponse = build_opener(NoRedirect()).open(request, timeout=20)
with response:
    payload: object = json.loads(response.read(1_048_577))
if not isinstance(payload, dict):
    raise ValueError("Invalid bootstrap response")
fields: Mapping[object, object] = payload
raw_users = fields.get("results")
if not isinstance(raw_users, list):
    raise ValueError("Invalid bootstrap users")
users: Sequence[object] = raw_users
if len(users) != 1 or not isinstance(users[0], dict):
    raise ValueError("Bootstrap administrator is not unique")
user: Mapping[object, object] = users[0]
if user.get("is_superuser") is not True or user.get("is_active") is not True:
    raise ValueError("Bootstrap administrator is not active")
"""
    result = subprocess.run(
        [
            'docker',
            'compose',
            '--env-file',
            str(env_file),
            '-f',
            'docker-compose.yml',
            '-f',
            'docker-compose.gcp.yml',
            '-f',
            'docker-compose.authentik.yml',
            'exec',
            '-T',
            'authentik-worker',
            'python',
            '-c',
            probe,
        ],
        capture_output=True,
        timeout=40,
    )
    if result.returncode:
        raise ValueError('Bootstrap is not ready; leave the proxy stopped and retry')
    print(json.dumps({'bootstrap_ready': True}))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        # Do not forward an administrator token to a redirect target.
        return None


class AuthentikAPI:
    def __init__(self, origin: str, token: str) -> None:
        self.origin = origin
        self.token = token
        self.opener = build_opener(NoRedirect())

    def request_text(
        self, method: str, path: str, body: JSONObject | ProviderSettings | None = None
    ) -> str:
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError(
                'API paths must be relative to the configured identity host'
            )
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Accept': 'application/json',
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        request = Request(self.origin + path, data=data, headers=headers, method=method)
        try:
            response: HTTPResponse = self.opener.open(request, timeout=30)
            with response:
                result = response.read(1_048_577)
                if len(result) > 1_048_576:
                    raise ValueError('API response exceeds the expected size')
                try:
                    return result.decode()
                except UnicodeError:
                    raise ValueError('Invalid API text encoding') from None
        except HTTPError as exc:
            # Response bodies can contain submitted credentials. Never print them.
            raise ValueError(
                f'authentik {method} {path.split("?")[0]} returned HTTP {exc.code}'
            ) from None
        except URLError:
            raise ValueError(
                'Could not reach the configured authentik HTTPS endpoint'
            ) from None

    def request(
        self, method: str, path: str, body: JSONObject | ProviderSettings | None = None
    ) -> JSONObject:
        content = self.request_text(method, path, body)
        return decode_object(content) if content else {}

    def lookup(self, resource: str, **filters: str | int) -> JSONObject | None:
        if resource == 'core/applications':
            # The normal list applies launch policies even for administrators.
            filters['superuser_full_list'] = 'true'
        response = self.request('GET', f'/api/v3/{resource}/?{urlencode(filters)}')
        results = array(response['results'])
        pagination = object_value(response.get('pagination', {}))
        if len(results) > 1 or pagination.get('next'):
            raise ValueError(
                f'Ambiguous existing {resource}; refusing to change objects'
            )
        return object_value(results[0]) if results else None

    def upsert(
        self,
        resource: str,
        body: JSONObject | ProviderSettings,
        value: str,
        key: str = 'name',
    ) -> JSONObject:
        existing = self.lookup(resource, **{key: value})
        if existing:
            identity = (
                string(existing['slug'])
                if resource in ('core/applications', 'flows/instances')
                else identifier(existing['pk'])
            )
            return self.request('PATCH', f'/api/v3/{resource}/{identity}/', body)
        return self.request('POST', f'/api/v3/{resource}/', body)


def provider_settings(
    app_origin: str, flows: FlowFields, mapping: str, certificate: str
) -> ProviderSettings:
    return {
        'name': PROVIDER_NAME,
        **flows,
        'acs_url': f'{app_origin}/api/auth/saml/acs',
        'audience': f'{app_origin}/api/auth/saml/metadata',
        'issuer_override': '',
        'sp_binding': 'post',
        'property_mappings': [mapping],
        'name_id_mapping': None,
        'default_name_id_policy': PERSISTENT_NAME_ID,
        'signing_kp': certificate,
        'sign_assertion': True,
        'sign_response': False,
        'signature_algorithm': 'http://www.w3.org/2001/04/xmldsig-more#rsa-sha256',
        'digest_algorithm': 'http://www.w3.org/2001/04/xmlenc#sha256',
        'verification_kp': None,
        'encryption_kp': None,
        'assertion_valid_not_before': 'minutes=-1',
        'assertion_valid_not_on_or_after': 'minutes=5',
        'session_valid_not_on_or_after': 'hours=8',
        'default_relay_state': '',
        'sls_url': '',
    }


def validate_metadata(metadata: str, certificate_pem: str, identity_origin: str) -> str:
    if '<!DOCTYPE' in metadata.upper() or '<!ENTITY' in metadata.upper():
        raise ValueError('Unexpected XML declaration in IdP metadata')
    document = ElementTree.fromstring(metadata)
    base = f'{identity_origin}/application/saml/openhands/'
    if document.get('entityID') != base + 'metadata/':
        raise ValueError('IdP metadata issuer differs from the configured trust')
    sso = document.findall('md:IDPSSODescriptor/md:SingleSignOnService', NS)
    if not any(
        item.get('Binding') == 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect'
        and item.get('Location') == base
        for item in sso
    ):
        raise ValueError('IdP metadata is missing the expected Redirect SSO endpoint')
    formats = document.findall('md:IDPSSODescriptor/md:NameIDFormat', NS)
    if not any(item.text == PERSISTENT_NAME_ID for item in formats):
        raise ValueError('IdP metadata does not support persistent NameIDs')
    pem_der = base64.b64decode(
        ''.join(
            line
            for line in certificate_pem.splitlines()
            if not line.startswith('-----')
        ),
        validate=True,
    )
    signing = document.findall(
        'md:IDPSSODescriptor/md:KeyDescriptor[@use="signing"]/ds:KeyInfo/ds:X509Data/ds:X509Certificate',
        NS,
    )
    if (
        len(signing) != 1
        or base64.b64decode(''.join((signing[0].text or '').split()), validate=True)
        != pem_der
    ):
        raise ValueError(
            'Metadata signing certificate does not match the exported trust'
        )
    return hashlib.sha256(pem_der).hexdigest()


def provision_demo_users(
    api: AuthentikAPI,
    private_dir: Path,
    demos: list[DemoCredentials],
    group_pk: str,
) -> list[UserSummary]:
    # Persist intent before creation, then bind it to the returned immutable ID.
    # A retry can finish initial password setup without resetting existing passwords.
    pending_file = private_dir / 'authentik-pending-passwords.json'
    pending = pending_passwords(pending_file)
    users: list[UserSummary] = []
    for demo in demos:
        existing_fields = api.lookup('core/users', username=demo['username'])
        existing = UserResponse.parse(existing_fields) if existing_fields else None
        if existing and (
            existing.email != demo['email'] or existing.path != 'users/openhands-demo'
        ):
            raise ValueError('Existing demo username belongs to another identity')
        if not existing:
            pending[demo['username']] = {'email': demo['email']}
            private_write(pending_file, json.dumps(pending) + '\n')
        user = UserResponse.parse(
            api.request(
                'PATCH' if existing else 'POST',
                f'/api/v3/core/users/{existing.pk}/'
                if existing
                else '/api/v3/core/users/',
                {
                    'username': demo['username'],
                    'name': demo['name'],
                    'email': demo['email'],
                    'is_active': True,
                    'type': 'internal',
                    'path': 'users/openhands-demo',
                    'groups': [group_pk],
                    'roles': [],
                },
            )
        )
        if user.is_superuser:
            raise ValueError('Demo user unexpectedly has superuser access')
        if demo['username'] in pending:
            operation = pending[demo['username']]
            if operation['email'] != demo['email'] or (
                'pk' in operation
                and (operation['pk'], operation['uid']) != (user.pk, user.uid)
            ):
                raise ValueError(
                    'Pending password operation belongs to another identity'
                )
            pending[demo['username']] = {
                'email': demo['email'],
                'pk': user.pk,
                'uid': user.uid,
            }
            private_write(pending_file, json.dumps(pending) + '\n')
            api.request(
                'POST',
                f'/api/v3/core/users/{user.pk}/set_password_hash/',
                {'password': password_hash(demo['password'])},
            )
            del pending[demo['username']]
            private_write(pending_file, json.dumps(pending) + '\n')
        users.append(
            {
                'username': user.username,
                'id': user.pk,
                'uid': user.uid,
                'is_superuser': user.is_superuser,
            }
        )
    return users


def configure_authorization_flow(api: AuthentikAPI) -> str:
    """Scope the pinned IdP's ForceAuthn marker cleanup to this application."""
    flow = api.upsert(
        'flows/instances',
        {
            'slug': AUTHORIZATION_FLOW,
            'name': 'OpenHands provider authorization',
            'title': 'Redirecting to OpenHands',
            'designation': 'authorization',
            'authentication': 'require_authenticated',
            'policy_engine_mode': 'all',
        },
        AUTHORIZATION_FLOW,
        key='slug',
    )
    policy = api.upsert(
        'policies/expression',
        {
            'name': REAUTH_POLICY,
            'expression': REAUTH_EXPRESSION,
            'execution_logging': False,
        },
        REAUTH_POLICY,
    )
    binding = api.lookup(
        'policies/bindings', target=string(flow['policybindingmodel_ptr_id']), order=0
    )
    body: JSONObject = {
        'target': identifier(flow['pk']),
        'policy': policy['pk'],
        'order': 0,
        'enabled': True,
        'negate': False,
        'failure_result': False,
    }
    if binding:
        if binding['policy'] != policy['pk']:
            raise ValueError('Existing authorization binding is not owned by setup')
        api.request('PATCH', f'/api/v3/policies/bindings/{binding["pk"]}/', body)
    else:
        api.request('POST', '/api/v3/policies/bindings/', body)
    return string(flow['pk'])


def provision(
    api: AuthentikAPI, env_file: Path, private_dir: Path, app_host: str
) -> None:
    credentials = decode_object((private_dir / 'credentials.json').read_text())
    group = api.upsert(
        'core/groups',
        {
            'name': GROUP_NAME,
            'is_superuser': False,
            'parents': [],
            'roles': [],
            'attributes': {
                'goauthentik.io/user/can-change-email': False,
                'goauthentik.io/user/can-change-username': False,
            },
        },
        GROUP_NAME,
    )
    users = provision_demo_users(
        api,
        private_dir,
        [demo_credentials(value) for value in array(credentials['saml_users'])],
        string(group['pk']),
    )
    flows: FlowFields = {}
    defaults: tuple[
        tuple[Literal['authentication_flow', 'invalidation_flow'], str], ...
    ] = (
        ('authentication_flow', 'default-authentication-flow'),
        ('invalidation_flow', 'default-provider-invalidation-flow'),
    )
    for field, slug in defaults:
        flow = api.lookup('flows/instances', slug=slug)
        if not flow:
            raise ValueError(f'Default flow is not ready: {slug}')
        flows[field] = string(flow['pk'])
    flows['authorization_flow'] = configure_authorization_flow(api)
    certificate = api.lookup('crypto/certificatekeypairs', name=CERTIFICATE_NAME)
    if not certificate:
        certificate = api.request(
            'POST',
            '/api/v3/crypto/certificatekeypairs/generate/',
            {'common_name': CERTIFICATE_NAME, 'validity_days': 365, 'alg': 'rsa'},
        )
    if (
        not boolean(certificate['private_key_available'])
        or string(certificate['key_type']) != 'rsa'
    ):
        raise ValueError('Existing SAML certificate is not an RSA signing keypair')
    mapping = api.upsert(
        'propertymappings/provider/saml',
        {
            'name': MAPPING_NAME,
            'saml_name': 'email',
            'friendly_name': 'email',
            'expression': 'return request.user.email',
        },
        MAPPING_NAME,
    )
    app_origin = f'https://{hostname(app_host)}'
    provider = api.upsert(
        'providers/saml',
        provider_settings(
            app_origin, flows, string(mapping['pk']), string(certificate['pk'])
        ),
        PROVIDER_NAME,
    )
    application = api.upsert(
        'core/applications',
        {
            'name': PROVIDER_NAME,
            'slug': 'openhands',
            'provider': provider['pk'],
            'meta_launch_url': f'{app_origin}/login',
            'policy_engine_mode': 'any',
        },
        'openhands',
        key='slug',
    )
    binding = api.lookup('policies/bindings', target=string(application['pk']), order=0)
    binding_body: JSONObject = {
        'target': application['pk'],
        'group': group['pk'],
        'order': 0,
        'enabled': True,
        'negate': False,
        'failure_result': False,
    }
    if binding:
        if binding['group'] != group['pk']:
            raise ValueError('Existing application binding is not owned by demo setup')
        api.request(
            'PATCH', f'/api/v3/policies/bindings/{binding["pk"]}/', binding_body
        )
    else:
        api.request('POST', '/api/v3/policies/bindings/', binding_body)
    pem = api.request_text(
        'GET',
        f'/api/v3/crypto/certificatekeypairs/{certificate["pk"]}/view_certificate/?download',
    )
    # The public application metadata URL redirects to this same-host API action.
    # Request the action directly so administrator requests never follow redirects.
    metadata = api.request_text(
        'GET', f'/api/v3/providers/saml/{provider["pk"]}/metadata/?download'
    )
    fingerprint = validate_metadata(metadata, pem, api.origin)
    details = subprocess.run(
        ['openssl', 'x509', '-noout', '-text'],
        input=pem,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if (
        'Public Key Algorithm: rsaEncryption' not in details
        or 'Public-Key: (4096 bit)' not in details
    ):
        raise ValueError('SAML signing certificate must be RSA 4096')
    subprocess.run(
        ['openssl', 'x509', '-noout', '-checkend', '86400'],
        input=pem,
        capture_output=True,
        text=True,
        check=True,
    )
    private_write(private_dir / 'authentik-idp-certificate.pem', pem)
    update_env(env_file, {'NATIVE_SAML_ENABLED': 'true'})
    summary = {
        'configured': True,
        'idp_metadata_url': f'{api.origin}/application/saml/openhands/metadata/',
        'sp_metadata_url': f'{app_origin}/api/auth/saml/metadata',
        'signing_certificate_sha256': fingerprint,
        'provider_id': provider['pk'],
        'authorization_flow': AUTHORIZATION_FLOW,
        'users': users,
    }
    private_write(
        private_dir / 'authentik-summary.json', json.dumps(summary, indent=2) + '\n'
    )
    print(json.dumps(summary))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'action', choices=('prepare', 'check-bootstrap', 'provision', 'finalize')
    )
    parser.add_argument('--env-file', type=Path, default=Path('.gcp/remote.env'))
    parser.add_argument('--private-dir', type=Path, default=Path('.gcp'))
    parser.add_argument('--app-host')
    parser.add_argument('--identity-host')
    parser.add_argument(
        '--token-file',
        type=Path,
        help='Optional file containing an administrator API token for a later reconfiguration',
    )
    args = parser.parse_args(namespace=Arguments())
    try:
        env = read_env(args.env_file)
        app_host = hostname(args.app_host or env.get('OPENHANDS_HOST', ''))
        identity_host = hostname(args.identity_host or env.get('AUTHENTIK_HOST', ''))
        if args.action == 'prepare':
            prepare(args.env_file, args.private_dir, app_host, identity_host)
            return
        if args.action == 'check-bootstrap':
            check_bootstrap(args.env_file)
            return
        bootstrap_file = args.private_dir / 'authentik-bootstrap.env'
        token = (
            args.token_file.read_text().strip()
            if args.token_file
            else read_env(bootstrap_file).get('AUTHENTIK_BOOTSTRAP_TOKEN')
        )
        if not token:
            raise ValueError(
                'Bootstrap token is revoked; supply a temporary administrator token file for reconfiguration'
            )
        api = AuthentikAPI(f'https://{identity_host}', token)
        if args.action == 'provision':
            provision(api, args.env_file, args.private_dir, app_host)
        else:
            api.request('DELETE', f'/api/v3/core/tokens/{BOOTSTRAP_TOKEN_ID}/')
            private_write(
                bootstrap_file, '# Bootstrap token revoked; keep this file empty.\n'
            )
            print(
                json.dumps({'bootstrap_token_revoked': True, 'recreate_worker': True})
            )
    except ValueError as exc:
        parser.exit(1, f'{exc}\n')
    except (OSError, KeyError, subprocess.SubprocessError) as exc:
        # Do not expose request bodies, response bodies, or subprocess output.
        parser.exit(
            1,
            f'authentik setup failed ({type(exc).__name__}); private data was not printed.\n',
        )


if __name__ == '__main__':
    main()
