"""Route-level tests for organization-shared secrets endpoints.

Verifies that ``require_permission`` enforces the MANAGE_ORG_SECRETS /
VIEW_ORG_SETTINGS permissions on the org-secrets CRUD routes, and that the
handlers delegate correctly to ``OrgSecretsStore``. The store itself is
covered by ``tests/unit/storage/test_org_secrets_store.py``; these tests
focus on the HTTP/permission layer.
"""

import logging
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


@pytest.fixture
async def validation_store(monkeypatch, async_session_maker, create_org):
    """Real PostgreSQL secrets; replace only auth and service construction."""
    from pydantic import SecretStr

    from openhands.app_server.services.jwt_service import JwtService
    from openhands.app_server.utils.encryption_key import EncryptionKey
    from storage.org_secrets_store import OrgSecretsStore

    monkeypatch.setattr(
        'storage.org_secrets_store.a_session_maker', async_session_maker
    )
    org = create_org()
    jwt = JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('test-key'), active=True)]
    )
    store = OrgSecretsStore(org.id, jwt)
    await store.create_shared(
        name='ORG_TOKEN',
        value='fake-original',
        description='original',
        created_by_user_id=TEST_USER_ID,
    )
    return store


@pytest.fixture
def validation_client(mock_app, mock_owner_role, validation_store):
    with (
        patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=mock_owner_role),
        ),
        _patch_org_store_get_instance(validation_store) as factory,
        TestClient(mock_app, raise_server_exceptions=False) as client,
    ):
        yield client, factory


class TestOrgSecretRequestValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'value',
        [
            'fake-attempted-canary',
            None,
            {'nested': 'fake-attempted-canary'},
            ['fake-attempted-canary'],
            123,
        ],
    )
    @pytest.mark.parametrize(
        'metadata', [{}, {'name': 'RENAMED', 'description': 'changed'}]
    )
    async def test_put_rejects_value_without_mutation_or_disclosure(
        self, validation_store, validation_client, caplog, value, metadata
    ):
        caplog.set_level(logging.DEBUG)
        client, factory = validation_client
        response = client.put(
            f'/api/organizations/{validation_store.org_id}/secrets/ORG_TOKEN',
            json={**metadata, 'value': value},
        )
        assert response.status_code == 422
        assert any(
            error['loc'] == ['body'] and error['type'] == 'extra_forbidden'
            for error in response.json()['detail']
        )
        assert 'fake-attempted-canary' not in response.text
        assert 'fake-attempted-canary' not in caplog.text
        factory.assert_not_awaited()
        values = await validation_store.load_shared_values()
        assert set(values) == {'ORG_TOKEN'}
        assert values['ORG_TOKEN'].secret.get_secret_value() == 'fake-original'
        assert values['ORG_TOKEN'].description == 'original'

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'name', ['', '1TOKEN', 'BAD-NAME', 'BAD NAME', 'A\nB', 'éTOKEN', 'TOKEN=oops']
    )
    @pytest.mark.parametrize('method', ['post', 'put'])
    async def test_invalid_names_return_client_error(
        self, validation_store, validation_client, caplog, name, method
    ):
        caplog.set_level(logging.DEBUG)
        client, factory = validation_client
        url = f'/api/organizations/{validation_store.org_id}/secrets'
        payload = {'name': name}
        if method == 'post':
            payload['value'] = 'fake-create-canary'
        else:
            url += '/ORG_TOKEN'
        response = getattr(client, method)(url, json=payload)
        assert response.status_code == 422
        assert 'fake-create-canary' not in response.text
        assert 'fake-create-canary' not in caplog.text
        factory.assert_not_awaited()
        values = await validation_store.load_shared_values()
        assert set(values) == {'ORG_TOKEN'}
        assert values['ORG_TOKEN'].secret.get_secret_value() == 'fake-original'

    def test_create_type_error_does_not_echo_secret_payload(
        self, validation_store, validation_client, caplog
    ):
        caplog.set_level(logging.DEBUG)
        client, factory = validation_client
        response = client.post(
            f'/api/organizations/{validation_store.org_id}/secrets',
            json={'name': 'NEW_TOKEN', 'value': {'nested': 'fake-invalid-type-canary'}},
        )
        assert response.status_code == 422
        assert 'fake-invalid-type-canary' not in response.text
        assert 'fake-invalid-type-canary' not in caplog.text
        factory.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'name', ['TOKEN', '_TOKEN', 'a1_token', 'Z', '_', 'lower_case']
)
async def test_valid_names_and_metadata_updates_still_work(
    validation_store, validation_client, name
):
    client, _ = validation_client
    url = f'/api/organizations/{validation_store.org_id}/secrets'
    response = client.post(url, json={'name': name, 'value': 'fake-valid-value'})
    assert response.status_code == 201
    assert 'fake-valid-value' not in response.text
    response = client.put(
        url + '/' + name, json={'name': name + '_2', 'description': 'updated'}
    )
    assert response.status_code == 200
    values = await validation_store.load_shared_values()
    assert name not in values
    assert values[name + '_2'].secret.get_secret_value() == 'fake-valid-value'
    assert values[name + '_2'].description == 'updated'


@pytest.mark.parametrize(
    'payload',
    [
        {'fake-unknown-field-canary': {'nested': 'fake-unknown-field-canary'}},
        {'name': 123, 'value': 'fake-unknown-field-canary'},
        {'description': {'nested': 'fake-unknown-field-canary'}},
        ['fake-unknown-field-canary'],
    ],
)
def test_update_validation_responses_omit_inputs(
    validation_store, validation_client, payload
):
    client, factory = validation_client
    response = client.put(
        f'/api/organizations/{validation_store.org_id}/secrets/ORG_TOKEN',
        json=payload,
    )
    assert response.status_code == 422
    assert 'fake-unknown-field-canary' not in response.text
    assert all(
        set(error) == {'type', 'loc', 'msg'} for error in response.json()['detail']
    )
    factory.assert_not_awaited()


def test_member_still_cannot_submit_value_updates(mock_app, org_id, mock_member_role):
    with patch(
        'server.auth.authorization.get_user_org_role',
        AsyncMock(return_value=mock_member_role),
    ):
        response = TestClient(mock_app).put(
            f'/api/organizations/{org_id}/secrets/ORG_TOKEN',
            json={'value': 'fake-member-canary'},
        )
    assert response.status_code == 403
    assert 'fake-member-canary' not in response.text


def test_update_schema_advertises_metadata_only(mock_app):
    schema = mock_app.openapi()['components']['schemas']['OrgSecretUpdate']
    assert set(schema['properties']) == {'name', 'description'}
    assert schema['additionalProperties'] is False


@pytest.mark.parametrize(
    'content_type,body',
    [
        ('text/plain', '{"name":"NEW_TOKEN","value":"fake-raw-body-canary"}'),
        ('application/json', '{"name":"NEW_TOKEN","value":"fake-raw-body-canary"'),
    ],
)
def test_raw_request_validation_does_not_echo_credentials(
    validation_store, validation_client, caplog, content_type, body
):
    caplog.set_level(logging.DEBUG)
    client, factory = validation_client
    response = client.post(
        f'/api/organizations/{validation_store.org_id}/secrets',
        content=body,
        headers={'Content-Type': content_type},
    )
    assert response.status_code == 422
    assert 'fake-raw-body-canary' not in response.text
    assert 'fake-raw-body-canary' not in caplog.text
    assert all(
        set(error) == {'type', 'loc', 'msg'} for error in response.json()['detail']
    )
    factory.assert_not_awaited()


@pytest.mark.parametrize('method', ['post', 'put'])
def test_member_cannot_submit_invalid_names(mock_app, org_id, mock_member_role, method):
    with (
        patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=mock_member_role),
        ),
        _patch_org_store_get_instance(MagicMock()) as factory,
    ):
        url = f'/api/organizations/{org_id}/secrets'
        payload = {'name': 'BAD-NAME'}
        if method == 'post':
            payload['value'] = 'fake-member-invalid-name'
        else:
            url += '/ORG_TOKEN'
        response = getattr(TestClient(mock_app), method)(url, json=payload)
    assert response.status_code == 403
    assert 'fake-member-invalid-name' not in response.text
    factory.assert_not_awaited()
