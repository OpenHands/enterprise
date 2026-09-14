"""Installation selection stays at composition and protocol boundaries."""

import ast
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from fastapi import Request

from server.auth import auth_config
from server.auth.composition import build_auth_services, get_auth_services
from server.auth.keycloak_request_auth import KeycloakRequestAuth
from server.auth.openhands_request_auth import OpenHandsRequestAuth

# Each allowed file owns installation configuration, startup, or durable mode proof.
INSTALLATION_BOUNDARIES = {
    'server/auth/auth_config.py',
    'server/auth/composition.py',
    'server/auth/server_wiring.py',
    'server/auth/bootstrap.py',
    'server/auth/ancillary_config.py',
    'server/config.py',
}
# These guards reject an inactive protocol before parsing its credentials or clients.
PROTOCOL_BOUNDARIES = {
    # Direct mode remains inactive until the password login layer.
    'server/app_lifespan/saas_app_lifespan_service.py': {
        'SaasAppLifespanService.__aenter__',
        '_require_supported_auth_mode',
    },
    'server/auth/keycloak_manager.py': {'require_keycloak'},
    'server/routes/auth.py': {'keycloak_callback', 'keycloak_offline_callback'},
    'server/routes/native_auth.py': {'NativeAuthRoute.get_route_handler.handler'},
}
MODE_NAMES = {'ENABLE_KEYCLOAK', 'AUTH_MODE'}


class _ModeImports(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names = set(MODE_NAMES)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in MODE_NAMES:
                self.names.add(alias.asname or alias.name)


class _ModeAccess(ast.NodeVisitor):
    def __init__(self, names: set[str], allowed_functions: set[str]) -> None:
        self.names = names
        self.allowed_functions = allowed_functions
        self.scope: list[str] = []
        self.violations: list[int] = []

    def _visit_scope(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node)

    def _reject(self, line: int) -> None:
        if '.'.join(self.scope) not in self.allowed_functions:
            self.violations.append(line)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if not self.allowed_functions and any(
            alias.name in MODE_NAMES or alias.name == '*' for alias in node.names
        ):
            self.violations.append(node.lineno)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.names:
            self._reject(node.lineno)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in MODE_NAMES:
            self._reject(node.lineno)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if node.value in MODE_NAMES:
            # Includes getenv, environ.get and getattr indirection.
            self._reject(node.lineno)


def forbidden_mode_access(source: str, path: str) -> list[int]:
    if path in INSTALLATION_BOUNDARIES:
        return []
    tree = ast.parse(source)
    imports = _ModeImports()
    imports.visit(tree)
    access = _ModeAccess(imports.names, PROTOCOL_BOUNDARIES.get(path, set()))
    access.visit(tree)
    return access.violations


def test_auth_selection_does_not_escape_owned_boundaries() -> None:
    root = Path(__file__).resolve().parents[4]
    paths = [root / 'saas_server.py', root / 'run_maintenance_tasks.py']
    for package in (
        'server',
        'storage',
        'integrations',
        'openhands',
        'sync',
        'analytics',
        'utils',
    ):
        paths.extend((root / package).rglob('*.py'))
    failures = {
        str(path.relative_to(root)): lines
        for path in paths
        if (
            lines := forbidden_mode_access(
                path.read_text(), str(path.relative_to(root))
            )
        )
    }
    assert failures == {}


@pytest.mark.parametrize(
    'source',
    [
        'from server.auth.auth_config import ENABLE_KEYCLOAK as external; enabled = external',
        "import os; enabled = os.getenv('ENABLE_KEYCLOAK')",
        "import os; enabled = os.environ.get('ENABLE_KEYCLOAK')",
        "enabled = getattr(config, 'ENABLE_KEYCLOAK')",
        'import server.auth.auth_config as selected; enabled = selected.ENABLE_KEYCLOAK',
        'enabled = auth_config.AUTH_MODE == "native"',
    ],
)
def test_mode_guard_catches_aliases_and_environment_reads(source: str) -> None:
    assert forbidden_mode_access(source, 'server/routes/application.py')


@pytest.fixture
def selection_cache() -> Iterator[None]:
    get_auth_services.cache_clear()
    yield
    get_auth_services.cache_clear()


def test_services_are_fixed_and_independent_of_request_state(
    selection_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_config, 'ENABLE_KEYCLOAK', True)
    keycloak = get_auth_services()
    assert type(keycloak.requests) is KeycloakRequestAuth
    monkeypatch.setattr(auth_config, 'ENABLE_KEYCLOAK', False)
    assert get_auth_services() is keycloak
    direct = build_auth_services()
    assert type(direct.requests) is OpenHandsRequestAuth
    assert direct.requests is not keycloak.requests
    with pytest.raises(FrozenInstanceError):
        setattr(keycloak, 'requests', direct.requests)


@pytest.mark.parametrize(
    'headers,cookie,expected',
    [
        ([(b'authorization', b'Basic malformed')], 'cookie-key', 'cookie-key'),
        ([(b'authorization', b'')], 'cookie-key', 'cookie-key'),
        ([(b'authorization', b'Bearer ')], 'cookie-key', ''),
        ([(b'authorization', b'Bearer explicit')], 'cookie-key', 'explicit'),
    ],
)
def test_keycloak_header_parsing_retains_legacy_precedence(
    headers: list[tuple[bytes, bytes]], cookie: str, expected: str
) -> None:
    request = Request(
        {
            'type': 'http',
            'headers': [*headers, (b'cookie', f'api_key={cookie}'.encode())],
        }
    )
    assert KeycloakRequestAuth().api_key(request) == expected
    if expected != 'explicit':
        assert OpenHandsRequestAuth().api_key(request) is None


def test_protocol_import_alias_cannot_escape_protocol_guard() -> None:
    source = 'from server.auth.auth_config import ENABLE_KEYCLOAK as flag\ndef application():\n    return flag\n'
    assert forbidden_mode_access(source, 'server/routes/auth.py') == [3]
