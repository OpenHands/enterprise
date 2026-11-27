"""Route-level tests for the organization Agent Canvas Apps endpoints.

Verifies who may do what (a Super Admin approves and removes, every member
lists and switches apps on for themselves), what an approved app must look
like, that the stored code is the commit that was fetched, the instance switch
that gates every route, and the store error mapping. The store itself is
covered by ``tests/unit/storage/test_org_canvas_app_store.py``.
"""

import json
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from openhands.app_server.user_auth import get_user_id
from server.routes.org_canvas_apps import (
    org_canvas_apps_router,
    user_context_dependency,
    validate_canvas_apps_enabled,
)
from server.services.feature_flag_service import FeatureFlagService
from storage.feature_flag_store import FeatureFlagStore
from storage.org_canvas_app import StoredOrgCanvasApp
from storage.org_canvas_app_store import (
    OrgCanvasAppAlreadyExistsError,
    OrgCanvasAppNotFoundError,
)

TEST_USER_ID = str(uuid.uuid4())
FLAG = 'ENABLE_CANVAS_APPS'
BUNDLE = 'export function activate() {}\n'
MANIFEST = {
    'schema_version': 1,
    'name': 'board',
    'display_name': 'Board',
    'version': '0.1.0',
    'description': 'A team board.',
    'entrypoint': 'extension.js',
    'contributes': {'pages': [{'id': 'board', 'title': 'Board', 'path': '/board'}]},
}
MANAGED_BACKEND = {
    'schema_version': 1,
    'artifacts': {
        'linux-amd64': {
            'url': 'https://downloads.example/board-server.tar.gz',
            'sha256': 'a' * 64,
        }
    },
    'argv': ['{artifact_dir}/bin/board-server', '--port', '{port}'],
}
INSTALL_BODY = {
    'source': 'github:acme/canvas-apps',
    'ref': 'main',
    'repo_path': 'board',
}


@pytest.fixture
def org_id():
    return uuid.uuid4()


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(org_canvas_apps_router)
    app.dependency_overrides[get_user_id] = lambda: TEST_USER_ID
    app.dependency_overrides[user_context_dependency.dependency] = lambda: None
    return app


@pytest.fixture
def mock_app():
    """The router with the instance switch on."""
    app = _build_app()
    app.dependency_overrides[validate_canvas_apps_enabled] = lambda: None
    return app


def _role(name: str):
    role = MagicMock()
    role.name = name
    return role


def _patch_role(role_name: str | None):
    return patch(
        'server.auth.authorization.get_user_org_role',
        AsyncMock(return_value=_role(role_name) if role_name else None),
    )


def _patch_super_admin():
    return patch(
        'server.auth.authorization.get_user_super_role',
        AsyncMock(return_value=_role('admin')),
    )


def _store(**methods) -> MagicMock:
    store = MagicMock()
    for name, value in methods.items():
        setattr(store, name, value)
    return store


def _patch_store(store: MagicMock):
    return patch('server.routes.org_canvas_apps.OrgCanvasAppStore', return_value=store)


def _patch_org(exists: bool = True):
    return patch(
        'server.routes.org_canvas_apps.OrgStore.get_org_by_id',
        AsyncMock(return_value=MagicMock() if exists else None),
    )


def _patch_clone(clone_dir: Path | None, error: str = ''):
    return patch(
        'server.routes.org_canvas_apps._clone_marketplace_repo',
        AsyncMock(return_value=(clone_dir, error)),
    )


def _stored(name: str = 'board') -> StoredOrgCanvasApp:
    return StoredOrgCanvasApp(
        org_id=uuid.uuid4(),
        name=name,
        source='github:acme/canvas-apps',
        requested_ref='main',
        resolved_ref='a' * 40,
        repo_path=name,
        manifest={**MANIFEST, 'name': name},
        installed_at=datetime(2026, 11, 27, tzinfo=UTC),
    )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ['git', '-C', str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@dataclass
class ClonedRepo:
    """A cloned repository holding the ``board`` app in its ``board`` folder."""

    path: Path

    @property
    def package(self) -> Path:
        return self.path / 'board'

    def commit(self) -> str:
        """Record the files as they are now; returns the commit SHA."""
        _git(self.path, 'add', '--all')
        _git(
            self.path,
            '-c',
            'user.name=Test',
            '-c',
            'user.email=test@example.com',
            'commit',
            '--quiet',
            '--message',
            'app',
        )
        return _git(self.path, 'rev-parse', 'HEAD')


@pytest.fixture
def clone(tmp_path) -> ClonedRepo:
    repo = ClonedRepo(tmp_path / 'clone')
    repo.package.mkdir(parents=True)
    (repo.package / 'canvas-extension.json').write_text(json.dumps(MANIFEST))
    (repo.package / 'extension.js').write_text(BUNDLE)
    _git(repo.path, 'init', '--quiet')
    return repo


def _install(mock_app, org_id, body: dict | None = None):
    return TestClient(mock_app).post(
        f'/api/organizations/{org_id}/canvas-extensions/install',
        json=body or INSTALL_BODY,
    )


class TestListOrgCanvasApps:
    def test_non_member_gets_403(self, mock_app, org_id):
        with _patch_role(None):
            response = TestClient(mock_app).get(
                f'/api/organizations/{org_id}/canvas-extensions/installed'
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_member_lists_the_orgs_apps_with_their_own_enabled_state(
        self, mock_app, org_id
    ):
        # Arrange
        store = _store(
            list_for=AsyncMock(
                return_value=[(_stored('board'), True), (_stored('notes'), False)]
            )
        )

        # Act
        with _patch_role('member'), _patch_store(store):
            response = TestClient(mock_app).get(
                f'/api/organizations/{org_id}/canvas-extensions/installed'
            )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        store.list_for.assert_awaited_once_with(uuid.UUID(TEST_USER_ID))
        board, notes = response.json()['canvas_extensions']
        assert board == {
            'name': 'board',
            'version': '0.1.0',
            'description': 'A team board.',
            'enabled': True,
            'source': 'github:acme/canvas-apps',
            'requested_ref': 'main',
            'resolved_ref': 'a' * 40,
            'repo_path': 'board',
            'installed_at': '2026-11-27T00:00:00+00:00',
            'install_path': '',
            'manifest': board['manifest'],
        }
        assert board['manifest']['contributes']['pages'][0]['path'] == '/board'
        assert notes['enabled'] is False


class TestInstallOrgCanvasApp:
    @pytest.mark.parametrize('role', ['member', 'admin', 'owner'])
    def test_org_roles_cannot_install(self, mock_app, org_id, role):
        with _patch_role(role):
            response = _install(mock_app, org_id)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_super_admin_installs_the_commit_that_was_fetched(
        self, mock_app, org_id, clone
    ):
        # Arrange
        commit = clone.commit()
        store = _store(create=AsyncMock(return_value=None))

        # Act
        with (
            _patch_role(None),
            _patch_super_admin(),
            _patch_org(),
            _patch_store(store),
            _patch_clone(clone.path),
        ):
            response = _install(mock_app, org_id)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body['name'] == 'board'
        assert body['resolved_ref'] == commit
        # Approving an app switches it on for nobody.
        assert body['enabled'] is False
        (stored,) = store.create.call_args.args
        assert stored.org_id == org_id
        assert stored.bundle == BUNDLE.encode()
        assert stored.resolved_ref == commit
        assert not clone.path.exists()

    def test_app_with_a_managed_backend_is_refused(self, mock_app, org_id, clone):
        # Arrange
        (clone.package / 'canvas-extension.json').write_text(
            json.dumps({**MANIFEST, 'backend': MANAGED_BACKEND})
        )
        clone.commit()
        store = _store(create=AsyncMock())

        # Act
        with (
            _patch_role(None),
            _patch_super_admin(),
            _patch_org(),
            _patch_store(store),
            _patch_clone(clone.path),
        ):
            response = _install(mock_app, org_id)

        # Assert
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert 'managed backend' in response.json()['detail']
        store.create.assert_not_called()

    def test_path_without_an_app_is_refused(self, mock_app, org_id, clone):
        # Arrange
        clone.commit()
        store = _store(create=AsyncMock())

        # Act
        with (
            _patch_role(None),
            _patch_super_admin(),
            _patch_org(),
            _patch_store(store),
            _patch_clone(clone.path),
        ):
            response = _install(mock_app, org_id, {**INSTALL_BODY, 'repo_path': 'docs'})

        # Assert
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert 'canvas-extension.json' in response.json()['detail']
        store.create.assert_not_called()

    def test_entrypoint_outside_the_app_is_not_read(
        self, mock_app, org_id, clone, tmp_path
    ):
        # Arrange
        secret = tmp_path / 'server-secret.txt'
        secret.write_text('do-not-serve')
        (clone.package / 'extension.js').unlink()
        (clone.package / 'extension.js').symlink_to(secret)
        clone.commit()
        store = _store(create=AsyncMock())

        # Act
        with (
            _patch_role(None),
            _patch_super_admin(),
            _patch_org(),
            _patch_store(store),
            _patch_clone(clone.path),
        ):
            response = _install(mock_app, org_id)

        # Assert
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        store.create.assert_not_called()

    def test_source_with_embedded_credentials_is_refused(self, mock_app, org_id):
        # Arrange
        body = {**INSTALL_BODY, 'source': 'https://ghp_token@github.com/acme/apps'}

        # Act
        with (
            _patch_role(None),
            _patch_super_admin(),
            _patch_org(),
            _patch_clone(None) as clone_repo,
        ):
            response = _install(mock_app, org_id, body)

        # Assert
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        clone_repo.assert_not_called()

    def test_unreadable_source_does_not_leak_the_git_error(self, mock_app, org_id):
        # Arrange
        git_error = (
            "Git clone failed: fatal: unable to access 'https://x:s3cret@git.example/'"
        )

        # Act
        with (
            _patch_role(None),
            _patch_super_admin(),
            _patch_org(),
            _patch_clone(None, git_error),
        ):
            response = _install(mock_app, org_id)

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 's3cret' not in response.text

    def test_name_the_org_already_has_returns_409(self, mock_app, org_id, clone):
        # Arrange
        clone.commit()
        store = _store(
            create=AsyncMock(side_effect=OrgCanvasAppAlreadyExistsError('board'))
        )

        # Act
        with (
            _patch_role(None),
            _patch_super_admin(),
            _patch_org(),
            _patch_store(store),
            _patch_clone(clone.path),
        ):
            response = _install(mock_app, org_id)

        # Assert
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_unknown_org_returns_404(self, mock_app, org_id):
        with (
            _patch_role(None),
            _patch_super_admin(),
            _patch_org(exists=False),
            _patch_clone(None) as clone_repo,
        ):
            response = _install(mock_app, org_id)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        clone_repo.assert_not_called()


class TestSetOrgCanvasAppEnabled:
    def _patch(self, mock_app, org_id, enabled: bool = True):
        return TestClient(mock_app).patch(
            f'/api/organizations/{org_id}/canvas-extensions/installed/board',
            json={'enabled': enabled},
        )

    def test_non_member_gets_403(self, mock_app, org_id):
        with _patch_role(None):
            response = self._patch(mock_app, org_id)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize('enabled', [True, False])
    def test_member_switches_an_app_for_themselves(self, mock_app, org_id, enabled):
        # Arrange
        store = _store(set_enabled=AsyncMock(return_value=None))

        # Act
        with _patch_role('member'), _patch_store(store):
            response = self._patch(mock_app, org_id, enabled)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {'name': 'board', 'enabled': enabled}
        store.set_enabled.assert_awaited_once_with(
            uuid.UUID(TEST_USER_ID), 'board', enabled
        )

    def test_app_the_org_does_not_have_returns_404(self, mock_app, org_id):
        # Arrange
        store = _store(
            set_enabled=AsyncMock(side_effect=OrgCanvasAppNotFoundError('board'))
        )

        # Act
        with _patch_role('member'), _patch_store(store):
            response = self._patch(mock_app, org_id)

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestUninstallOrgCanvasApp:
    def _delete(self, mock_app, org_id):
        return TestClient(mock_app).delete(
            f'/api/organizations/{org_id}/canvas-extensions/installed/board'
        )

    @pytest.mark.parametrize('role', ['member', 'admin', 'owner'])
    def test_org_roles_cannot_uninstall(self, mock_app, org_id, role):
        with _patch_role(role):
            response = self._delete(mock_app, org_id)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_super_admin_uninstalls(self, mock_app, org_id):
        # Arrange
        store = _store(delete=AsyncMock(return_value=True))

        # Act
        with _patch_role(None), _patch_super_admin(), _patch_store(store):
            response = self._delete(mock_app, org_id)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        store.delete.assert_awaited_once_with('board')

    def test_app_the_org_does_not_have_returns_404(self, mock_app, org_id):
        # Arrange
        store = _store(delete=AsyncMock(return_value=False))

        # Act
        with _patch_role(None), _patch_super_admin(), _patch_store(store):
            response = self._delete(mock_app, org_id)

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestOrgCanvasAppBundle:
    def _get(self, mock_app, org_id):
        return TestClient(mock_app).get(
            f'/api/organizations/{org_id}/canvas-extensions/installed/board/bundle'
        )

    def test_member_loads_the_stored_entrypoint_as_javascript(self, mock_app, org_id):
        # Arrange
        store = _store(get_bundle=AsyncMock(return_value=BUNDLE.encode()))

        # Act
        with _patch_role('member'), _patch_store(store):
            response = self._get(mock_app, org_id)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.text == BUNDLE
        assert response.headers['content-type'].startswith('text/javascript')

    def test_app_the_org_does_not_have_returns_404(self, mock_app, org_id):
        # Arrange
        store = _store(get_bundle=AsyncMock(return_value=None))

        # Act
        with _patch_role('member'), _patch_store(store):
            response = self._get(mock_app, org_id)

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND


# --- Instance switch ---

BASE = '/api/organizations/{org_id}/canvas-extensions'
GATED_ROUTES = [
    ('GET', '/installed', None),
    ('POST', '/install', INSTALL_BODY),
    ('PATCH', '/installed/{name}', {'enabled': True}),
    ('DELETE', '/installed/{name}', None),
    ('GET', '/installed/{name}/bundle', None),
]


@pytest.fixture
def flag_db(async_session_maker):
    """Resolve the switch through the real service against the test database."""
    FeatureFlagService._cache.clear()
    FeatureFlagService._global_cache.clear()
    with patch('storage.feature_flag_store.a_session_maker', async_session_maker):
        yield
    FeatureFlagService._cache.clear()
    FeatureFlagService._global_cache.clear()


def test_every_route_is_gated():
    """GATED_ROUTES names exactly the router's routes, so a new route fails
    here until it is added to the 403 test below."""
    listed = {(method, BASE + suffix) for method, suffix, _body in GATED_ROUTES}
    routed = {
        (method, route.path)
        for route in org_canvas_apps_router.routes
        for method in route.methods
    }
    assert listed == routed


@pytest.fixture
def gated_client():
    """The router as deployed: the instance switch decides."""
    return AsyncClient(
        transport=ASGITransport(app=_build_app()), base_url='http://test'
    )


@pytest.mark.parametrize(('method', 'suffix', 'body'), GATED_ROUTES)
async def test_routes_return_403_until_the_switch_is_turned_on(
    gated_client, flag_db, org_id, method, suffix, body
):
    # Arrange
    url = (BASE + suffix).format(org_id=org_id, name='board')

    # Act
    with _patch_role(None), _patch_super_admin():
        async with gated_client as client:
            response = await client.request(method, url, json=body)

    # Assert
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert 'turned off' in response.json()['detail']


async def test_routes_answer_once_a_super_admin_turns_the_switch_on(
    gated_client, flag_db, org_id
):
    # Arrange
    await FeatureFlagStore.create_flag(key=FLAG, enabled=True)
    store = _store(list_for=AsyncMock(return_value=[]))

    # Act
    with _patch_role('member'), _patch_store(store):
        async with gated_client as client:
            response = await client.get(
                f'/api/organizations/{org_id}/canvas-extensions/installed'
            )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {'canvas_extensions': []}
