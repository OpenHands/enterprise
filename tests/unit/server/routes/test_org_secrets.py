"""Route-level tests for organization-shared secrets endpoints.

Verifies that ``require_permission`` enforces the MANAGE_ORG_SECRETS /
VIEW_ORG_SETTINGS permissions on the org-secrets CRUD routes, and that the
handlers delegate correctly to ``OrgSecretsStore``. The store itself is
covered by ``tests/unit/storage/test_org_secrets_store.py``; these tests
focus on the HTTP/permission layer.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from openhands.app_server.secrets.secrets_models import (
    CustomSecretScope,
    CustomSecretWithoutValue,
)
from openhands.app_server.user_auth import get_user_id
from server.routes.org_secrets import (
    OrgSecretAlreadyExistsError,
    OrgSecretNotFoundError,
    org_secrets_router,
)

TEST_USER_ID = str(uuid.uuid4())


@pytest.fixture
def org_id():
    return uuid.uuid4()


@pytest.fixture
def mock_app():
    """FastAPI app with org-secrets routes and a stubbed authenticated user."""
    app = FastAPI()
    app.include_router(org_secrets_router)
    app.dependency_overrides[get_user_id] = lambda: TEST_USER_ID
    return app


@pytest.fixture
def mock_owner_role():
    role = MagicMock()
    role.name = 'owner'
    return role


@pytest.fixture
def mock_admin_role():
    role = MagicMock()
    role.name = 'admin'
    return role


@pytest.fixture
def mock_member_role():
    role = MagicMock()
    role.name = 'member'
    return role


def _mock_store_instance():
    """A MagicMock standing in for an OrgSecretsStore instance."""
    return MagicMock()


def _patch_org_store_get_instance(mock_store):
    return patch(
        'server.routes.org_secrets.OrgSecretsStore.get_instance',
        AsyncMock(return_value=mock_store),
    )


# =============================================================================
# GET /api/organizations/{org_id}/secrets  (VIEW_ORG_SETTINGS)
# =============================================================================


class TestListOrgSecrets:
    def test_non_member_gets_403(self, mock_app, org_id):
        with patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=None),
        ):
            client = TestClient(mock_app)
            response = client.get(f'/api/organizations/{org_id}/secrets')

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_member_can_list(self, mock_app, org_id, mock_member_role):
        """All members (VIEW_ORG_SETTINGS) can list org-shared secrets."""
        items = [
            CustomSecretWithoutValue.model_construct(
                name='ORG_TOKEN',
                description='shared',
                scope=CustomSecretScope.ORGANIZATION,
            )
        ]
        store = MagicMock()
        store.list_shared = AsyncMock(return_value=items)
        with (
            patch(
                'server.auth.authorization.get_user_org_role',
                AsyncMock(return_value=mock_member_role),
            ),
            _patch_org_store_get_instance(store),
        ):
            client = TestClient(mock_app)
            response = client.get(f'/api/organizations/{org_id}/secrets')

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert len(body['items']) == 1
        assert body['items'][0]['name'] == 'ORG_TOKEN'
        assert body['items'][0]['scope'] == 'organization'


# =============================================================================
# POST /api/organizations/{org_id}/secrets  (MANAGE_ORG_SECRETS)
# =============================================================================


class TestCreateOrgSecret:
    def test_member_gets_403(self, mock_app, org_id, mock_member_role):
        """Members lack MANAGE_ORG_SECRETS and cannot create org secrets."""
        with patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=mock_member_role),
        ):
            client = TestClient(mock_app)
            response = client.post(
                f'/api/organizations/{org_id}/secrets',
                json={'name': 'X', 'value': 'v'},
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'manage_org_secrets' in response.json()['detail']

    def test_admin_can_create(self, mock_app, org_id, mock_admin_role):
        store = MagicMock()
        store.create_shared = AsyncMock(return_value=None)
        with (
            patch(
                'server.auth.authorization.get_user_org_role',
                AsyncMock(return_value=mock_admin_role),
            ),
            _patch_org_store_get_instance(store),
        ):
            client = TestClient(mock_app)
            response = client.post(
                f'/api/organizations/{org_id}/secrets',
                json={
                    'name': 'ORG_TOKEN',
                    'value': 'secret-value',
                    'description': 'a shared token',
                },
            )

        assert response.status_code == status.HTTP_201_CREATED
        store.create_shared.assert_called_once()
        call_kwargs = store.create_shared.call_args.kwargs
        assert call_kwargs['name'] == 'ORG_TOKEN'
        assert call_kwargs['value'] == 'secret-value'
        assert call_kwargs['description'] == 'a shared token'
        assert call_kwargs['created_by_user_id'] == TEST_USER_ID

    def test_duplicate_returns_409(self, mock_app, org_id, mock_admin_role):
        store = MagicMock()
        store.create_shared = AsyncMock(
            side_effect=OrgSecretAlreadyExistsError('exists')
        )
        with (
            patch(
                'server.auth.authorization.get_user_org_role',
                AsyncMock(return_value=mock_admin_role),
            ),
            _patch_org_store_get_instance(store),
        ):
            client = TestClient(mock_app)
            response = client.post(
                f'/api/organizations/{org_id}/secrets',
                json={'name': 'DUP', 'value': 'v'},
            )

        assert response.status_code == status.HTTP_409_CONFLICT


# =============================================================================
# PUT /api/organizations/{org_id}/secrets/{secret_name}  (MANAGE_ORG_SECRETS)
# =============================================================================


class TestUpdateOrgSecret:
    def test_member_gets_403(self, mock_app, org_id, mock_member_role):
        with patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=mock_member_role),
        ):
            client = TestClient(mock_app)
            response = client.put(
                f'/api/organizations/{org_id}/secrets/OLD',
                json={'name': 'NEW'},
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_owner_can_update(self, mock_app, org_id, mock_owner_role):
        store = MagicMock()
        store.update_shared = AsyncMock(return_value=None)
        with (
            patch(
                'server.auth.authorization.get_user_org_role',
                AsyncMock(return_value=mock_owner_role),
            ),
            _patch_org_store_get_instance(store),
        ):
            client = TestClient(mock_app)
            response = client.put(
                f'/api/organizations/{org_id}/secrets/OLD',
                json={'name': 'NEW', 'description': 'updated'},
            )

        assert response.status_code == status.HTTP_200_OK
        store.update_shared.assert_called_once_with(
            current_name='OLD', new_name='NEW', description='updated'
        )

    def test_not_found_returns_404(self, mock_app, org_id, mock_owner_role):
        store = MagicMock()
        store.update_shared = AsyncMock(side_effect=OrgSecretNotFoundError('missing'))
        with (
            patch(
                'server.auth.authorization.get_user_org_role',
                AsyncMock(return_value=mock_owner_role),
            ),
            _patch_org_store_get_instance(store),
        ):
            client = TestClient(mock_app)
            response = client.put(
                f'/api/organizations/{org_id}/secrets/OLD',
                json={'description': 'x'},
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# DELETE /api/organizations/{org_id}/secrets/{secret_name}  (MANAGE_ORG_SECRETS)
# =============================================================================


class TestDeleteOrgSecret:
    def test_member_gets_403(self, mock_app, org_id, mock_member_role):
        with patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=mock_member_role),
        ):
            client = TestClient(mock_app)
            response = client.delete(f'/api/organizations/{org_id}/secrets/TOKEN')

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_can_delete(self, mock_app, org_id, mock_admin_role):
        store = MagicMock()
        store.delete_shared = AsyncMock(return_value=None)
        with (
            patch(
                'server.auth.authorization.get_user_org_role',
                AsyncMock(return_value=mock_admin_role),
            ),
            _patch_org_store_get_instance(store),
        ):
            client = TestClient(mock_app)
            response = client.delete(f'/api/organizations/{org_id}/secrets/TOKEN')

        assert response.status_code == status.HTTP_200_OK
        store.delete_shared.assert_called_once_with('TOKEN')

    def test_not_found_returns_404(self, mock_app, org_id, mock_admin_role):
        store = MagicMock()
        store.delete_shared = AsyncMock(side_effect=OrgSecretNotFoundError('missing'))
        with (
            patch(
                'server.auth.authorization.get_user_org_role',
                AsyncMock(return_value=mock_admin_role),
            ),
            _patch_org_store_get_instance(store),
        ):
            client = TestClient(mock_app)
            response = client.delete(f'/api/organizations/{org_id}/secrets/TOKEN')

        assert response.status_code == status.HTTP_404_NOT_FOUND
