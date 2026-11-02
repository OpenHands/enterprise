"""Route-level tests for the organization MCP server listing endpoints.

Verifies the ``MANAGE_ORG_MCP`` / ``VIEW_ORG_SETTINGS`` split, the listing
validation (remote servers only, no OAuth, reserved names, organization-managed
credentials drawn from org-shared secrets), redaction on reads and on 422s,
and the store error mapping. The store itself is covered by
``tests/unit/storage/test_org_mcp_servers_store.py``.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.app_server.secrets.secrets_models import (
    CustomSecretScope,
    CustomSecretWithoutValue,
)
from openhands.app_server.user_auth import get_user_id
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE
from server.routes.org_mcp_servers import org_mcp_servers_router
from storage.org_mcp_servers import OrgMcpServer
from storage.org_mcp_servers_store import (
    OrgMcpServerAlreadyExistsError,
    OrgMcpServerInUseError,
    OrgMcpServerNotFoundError,
)

TEST_USER_ID = str(uuid.uuid4())
DATADOG_URL = 'https://dd.example/mcp'
STORED_TOKEN = 'dd-service-key'


@pytest.fixture
def org_id():
    return uuid.uuid4()


@pytest.fixture
def mock_app():
    app = FastAPI()
    app.include_router(org_mcp_servers_router)
    app.dependency_overrides[get_user_id] = lambda: TEST_USER_ID
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


def _patch_store(store: MagicMock):
    return patch('server.routes.org_mcp_servers.OrgMcpServersStore', return_value=store)


def _patch_shared_secrets(names: list[str]):
    secrets_store = MagicMock()
    secrets_store.list_shared = AsyncMock(
        return_value=[
            CustomSecretWithoutValue.model_construct(
                name=name, description=None, scope=CustomSecretScope.ORGANIZATION
            )
            for name in names
        ]
    )
    return patch(
        'server.routes.org_mcp_servers.OrgSecretsStore.get_instance',
        AsyncMock(return_value=secrets_store),
    )


def _stored_org_managed() -> OrgMcpServer:
    return OrgMcpServer(
        managed_by='org',
        server=MCPServer(
            url=DATADOG_URL,
            transport='http',
            auth={'strategy': 'bearer', 'value': STORED_TOKEN},
        ),
    )


def _stored_user_managed() -> OrgMcpServer:
    return OrgMcpServer(
        managed_by='user',
        server=MCPServer(
            url='https://bifrost.example/mcp',
            transport='http',
            auth={'strategy': 'bearer', 'value': '${BIFROST_TOKEN}'},
        ),
    )


def _store(**methods) -> MagicMock:
    store = MagicMock()
    for name, value in methods.items():
        setattr(store, name, value)
    return store


def _bearer_body(name: str = 'datadog', managed_by: str = 'user', token: str = '${DD}'):
    return {
        'name': name,
        'managed_by': managed_by,
        'server': {
            'url': DATADOG_URL,
            'transport': 'http',
            'auth': {'strategy': 'bearer', 'value': token},
        },
    }


class TestListOrgMcpServers:
    def test_non_member_gets_403(self, mock_app, org_id):
        with _patch_role(None):
            response = TestClient(mock_app).get(
                f'/api/organizations/{org_id}/mcp-servers'
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_member_lists_redacted_listings(self, mock_app, org_id):
        store = _store(
            list=AsyncMock(
                return_value={
                    'datadog': _stored_org_managed(),
                    'bifrost': _stored_user_managed(),
                }
            )
        )
        with _patch_role('member'), _patch_store(store):
            response = TestClient(mock_app).get(
                f'/api/organizations/{org_id}/mcp-servers'
            )

        assert response.status_code == status.HTTP_200_OK
        datadog, bifrost = response.json()['items']
        assert datadog['name'] == 'datadog'
        assert datadog['managed_by'] == 'org'
        assert datadog['server']['url'] == DATADOG_URL
        assert datadog['server']['auth']['value'] == REDACTED_SECRET_VALUE
        assert datadog['secret_names'] == []
        assert STORED_TOKEN not in response.text
        # The reference sits inside the masked credential, so the names are
        # listed separately: that is how a member learns what to connect with.
        assert bifrost['managed_by'] == 'user'
        assert bifrost['server']['auth']['value'] == REDACTED_SECRET_VALUE
        assert bifrost['secret_names'] == ['BIFROST_TOKEN']


class TestCreateOrgMcpServer:
    def test_member_gets_403(self, mock_app, org_id):
        with _patch_role('member'):
            response = TestClient(mock_app).post(
                f'/api/organizations/{org_id}/mcp-servers', json=_bearer_body()
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'manage_org_mcp' in response.json()['detail']

    def test_admin_creates_user_managed_listing(self, mock_app, org_id):
        store = _store(create=AsyncMock(return_value=None))
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).post(
                f'/api/organizations/{org_id}/mcp-servers',
                json=_bearer_body(token='${BIFROST_TOKEN}'),
            )

        assert response.status_code == status.HTTP_201_CREATED
        name, entry = store.create.call_args.args
        assert name == 'datadog'
        assert entry.managed_by == 'user'
        assert entry.server.url == DATADOG_URL
        assert entry.server.auth.value.get_secret_value() == '${BIFROST_TOKEN}'

    def test_owner_creates_org_managed_listing_with_inline_token(
        self, mock_app, org_id
    ):
        store = _store(create=AsyncMock(return_value=None))
        with _patch_role('owner'), _patch_store(store):
            response = TestClient(mock_app).post(
                f'/api/organizations/{org_id}/mcp-servers',
                json=_bearer_body(managed_by='org', token=STORED_TOKEN),
            )

        assert response.status_code == status.HTTP_201_CREATED
        _name, entry = store.create.call_args.args
        assert entry.managed_by == 'org'
        assert entry.server.auth.value.get_secret_value() == STORED_TOKEN

    def test_org_managed_listing_may_only_reference_shared_secrets(
        self, mock_app, org_id
    ):
        store = _store(create=AsyncMock(return_value=None))
        with (
            _patch_role('admin'),
            _patch_store(store),
            _patch_shared_secrets(['DD_SHARED']),
        ):
            client = TestClient(mock_app)
            rejected = client.post(
                f'/api/organizations/{org_id}/mcp-servers',
                json=_bearer_body(managed_by='org', token='${MY_PERSONAL_KEY}'),
            )
            accepted = client.post(
                f'/api/organizations/{org_id}/mcp-servers',
                json=_bearer_body(managed_by='org', token='${DD_SHARED}'),
            )

        assert rejected.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert 'MY_PERSONAL_KEY' in rejected.json()['detail']
        assert accepted.status_code == status.HTTP_201_CREATED
        store.create.assert_called_once()

    @pytest.mark.parametrize(
        'server, fragment',
        [
            ({'command': 'npx', 'args': ['server']}, 'remote'),
            (
                {
                    'url': DATADOG_URL,
                    'transport': 'http',
                    'auth': {'strategy': 'oauth2', 'authentication': {'type': 'oauth'}},
                },
                'OAuth',
            ),
            ({'transport': 'http'}, 'url'),
        ],
    )
    def test_rejects_unsupported_servers(self, mock_app, org_id, server, fragment):
        store = _store(create=AsyncMock(return_value=None))
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).post(
                f'/api/organizations/{org_id}/mcp-servers',
                json={'name': 'srv', 'managed_by': 'user', 'server': server},
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert fragment.lower() in response.text.lower()
        store.create.assert_not_called()

    @pytest.mark.parametrize('name', ['default', 'bad name', 'a/b', ''])
    def test_rejects_reserved_or_invalid_names(self, mock_app, org_id, name):
        store = _store(create=AsyncMock(return_value=None))
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).post(
                f'/api/organizations/{org_id}/mcp-servers', json=_bearer_body(name=name)
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        store.create.assert_not_called()

    def test_validation_errors_do_not_echo_the_credential(self, mock_app, org_id):
        store = _store(create=AsyncMock(return_value=None))
        body = _bearer_body(managed_by='org', token='super-secret-token')
        body['server']['transport'] = 'carrier-pigeon'
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).post(
                f'/api/organizations/{org_id}/mcp-servers', json=body
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert 'super-secret-token' not in response.text

    def test_duplicate_returns_409(self, mock_app, org_id):
        store = _store(
            create=AsyncMock(side_effect=OrgMcpServerAlreadyExistsError('exists'))
        )
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).post(
                f'/api/organizations/{org_id}/mcp-servers', json=_bearer_body()
            )

        assert response.status_code == status.HTTP_409_CONFLICT


class TestUpdateOrgMcpServer:
    def test_member_gets_403(self, mock_app, org_id):
        with _patch_role('member'):
            response = TestClient(mock_app).put(
                f'/api/organizations/{org_id}/mcp-servers/datadog',
                json={'managed_by': 'org'},
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unknown_listing_returns_404(self, mock_app, org_id):
        store = _store(get=AsyncMock(return_value=None))
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).put(
                f'/api/organizations/{org_id}/mcp-servers/ghost',
                json={'managed_by': 'org'},
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_redacted_credential_keeps_the_stored_value(self, mock_app, org_id):
        store = _store(
            get=AsyncMock(return_value=_stored_org_managed()),
            update=AsyncMock(return_value=None),
        )
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).put(
                f'/api/organizations/{org_id}/mcp-servers/datadog',
                json={
                    'server': {
                        'url': DATADOG_URL,
                        'transport': 'http',
                        'timeout': 90,
                        'auth': {'strategy': 'bearer', 'value': REDACTED_SECRET_VALUE},
                    }
                },
            )

        assert response.status_code == status.HTTP_200_OK
        name, entry = store.update.call_args.args
        assert name == 'datadog'
        assert entry.managed_by == 'org'
        assert entry.server.timeout == 90
        assert entry.server.auth.value.get_secret_value() == STORED_TOKEN

    def test_management_change_keeps_the_stored_config(self, mock_app, org_id):
        store = _store(
            get=AsyncMock(return_value=_stored_org_managed()),
            update=AsyncMock(return_value=None),
        )
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).put(
                f'/api/organizations/{org_id}/mcp-servers/datadog',
                json={'managed_by': 'user'},
            )

        assert response.status_code == status.HTTP_200_OK
        _name, entry = store.update.call_args.args
        assert entry.managed_by == 'user'
        assert entry.server.url == DATADOG_URL
        assert entry.server.auth.value.get_secret_value() == STORED_TOKEN

    def test_referenced_listing_returns_409(self, mock_app, org_id):
        store = _store(
            get=AsyncMock(return_value=_stored_org_managed()),
            update=AsyncMock(side_effect=OrgMcpServerInUseError('datadog', ['ops'])),
        )
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).put(
                f'/api/organizations/{org_id}/mcp-servers/datadog',
                json={'managed_by': 'user'},
            )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert 'ops' in response.json()['detail']


class TestDeleteOrgMcpServer:
    def test_member_gets_403(self, mock_app, org_id):
        with _patch_role('member'):
            response = TestClient(mock_app).delete(
                f'/api/organizations/{org_id}/mcp-servers/datadog'
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_deletes(self, mock_app, org_id):
        store = _store(delete=AsyncMock(return_value=None))
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).delete(
                f'/api/organizations/{org_id}/mcp-servers/datadog'
            )

        assert response.status_code == status.HTTP_200_OK
        store.delete.assert_called_once_with('datadog')

    @pytest.mark.parametrize(
        'error, expected',
        [
            (OrgMcpServerNotFoundError('missing'), status.HTTP_404_NOT_FOUND),
            (OrgMcpServerInUseError('datadog', ['ops']), status.HTTP_409_CONFLICT),
        ],
    )
    def test_store_errors_map_to_status(self, mock_app, org_id, error, expected):
        store = _store(delete=AsyncMock(side_effect=error))
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).delete(
                f'/api/organizations/{org_id}/mcp-servers/datadog'
            )

        assert response.status_code == expected


def test_secret_str_never_appears_in_redacted_dump():
    """Guard the redaction helper the list endpoint relies on."""
    from storage.org_mcp_servers import redacted_org_mcp_server

    entry = OrgMcpServer(
        managed_by='org',
        server=MCPServer(
            url=DATADOG_URL,
            transport='http',
            headers={'X-Api-Key': SecretStr('header-secret')},
        ),
    )

    dump = redacted_org_mcp_server('datadog', entry)

    assert dump['headers']['X-Api-Key'] == REDACTED_SECRET_VALUE
