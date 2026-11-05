"""Route-level tests for the organization MCP server listing endpoints.

Verifies the ``MANAGE_ORG_MCP`` / ``VIEW_ORG_SETTINGS`` split, the listing
validation (remote or stdio servers, reserved names, organization-managed
credentials drawn from org-shared secrets, OAuth rules per management kind),
redaction on reads and on 422s, the member OAuth connection endpoints, and the
store error mapping. The store itself is covered by
``tests/unit/storage/test_org_mcp_servers_store.py``.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.app_server.secrets.secrets_models import (
    CustomSecretScope,
    CustomSecretWithoutValue,
)
from openhands.app_server.user_auth import get_user_id
from openhands.sdk.mcp.config import MCPOAuthStateResponse, MCPServer
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
OAUTH_URL = 'https://mcp.atlassian.example/v1/mcp'
OAUTH_STATE = {
    'tokens': {'access_token': 'admin-access', 'refresh_token': 'admin-refresh'},
    'client_info': {'client_id': 'registered-client'},
    'token_expires_at': 1_700_000_000.0,
}


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


def _patch_job_state(state: dict | None = None, error: HTTPException | None = None):
    """The finished OAuth job: its tokens, or the HTTP error reading it raises."""
    return patch(
        'server.routes.org_mcp_servers.completed_oauth_job_state',
        AsyncMock(
            return_value=MCPOAuthStateResponse.model_validate(state or OAUTH_STATE),
            side_effect=error,
        ),
    )


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


def _stored_stdio(managed_by: str = 'user', env: dict | None = None) -> OrgMcpServer:
    return OrgMcpServer(
        managed_by=managed_by,
        server=MCPServer(
            command='npx',
            args=['-y', '@slack/mcp'],
            env={'SLACK_BOT_TOKEN': '${SLACK_TOKEN}'} if env is None else env,
        ),
    )


def _oauth_server(state: dict | None = None, client_secret: str = 'shh') -> dict:
    return {
        'url': OAUTH_URL,
        'transport': 'http',
        'auth': {
            'strategy': 'oauth2',
            'authentication': {
                'type': 'oauth',
                'client_id': 'cid',
                'client_secret': client_secret,
                'scopes': ['read:jira-work'],
            },
            **({'state': state} if state is not None else {}),
        },
    }


def _stored_oauth(managed_by: str, state: dict | None) -> OrgMcpServer:
    return OrgMcpServer(
        managed_by=managed_by, server=MCPServer.model_validate(_oauth_server(state))
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


def _stdio_body(name: str = 'slack', managed_by: str = 'user', env: dict | None = None):
    return {
        'name': name,
        'managed_by': managed_by,
        'server': {
            'command': 'npx',
            'args': ['-y', '@slack/mcp'],
            'env': {'SLACK_BOT_TOKEN': '${SLACK_TOKEN}'} if env is None else env,
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
        # ``connected`` only means something for user-managed OAuth listings.
        assert datadog['connected'] is None
        assert bifrost['connected'] is None
        store.member_connected_names.assert_not_called()

    def test_member_sees_a_stdio_listing_with_its_env_masked(self, mock_app, org_id):
        store = _store(list=AsyncMock(return_value={'slack': _stored_stdio()}))
        with _patch_role('member'), _patch_store(store):
            response = TestClient(mock_app).get(
                f'/api/organizations/{org_id}/mcp-servers'
            )

        assert response.status_code == status.HTTP_200_OK
        (slack,) = response.json()['items']
        assert slack['server']['command'] == 'npx'
        assert slack['server']['args'] == ['-y', '@slack/mcp']
        # Env values are credentials: masked like a header, reference included.
        assert slack['server']['env'] == {'SLACK_BOT_TOKEN': REDACTED_SECRET_VALUE}
        assert '${SLACK_TOKEN}' not in response.text
        assert slack['secret_names'] == ['SLACK_TOKEN']

    def test_member_sees_whether_they_authorized_oauth_listings(self, mock_app, org_id):
        store = _store(
            list=AsyncMock(
                return_value={
                    'jira-shared': _stored_oauth('org', OAUTH_STATE),
                    'jira': _stored_oauth('user', None),
                    'confluence': _stored_oauth('user', None),
                }
            ),
            member_connected_names=AsyncMock(return_value={'jira'}),
        )
        with _patch_role('member'), _patch_store(store):
            response = TestClient(mock_app).get(
                f'/api/organizations/{org_id}/mcp-servers'
            )

        assert response.status_code == status.HTTP_200_OK
        items = {item['name']: item for item in response.json()['items']}
        assert items['jira-shared']['connected'] is None
        assert items['jira']['connected'] is True
        assert items['confluence']['connected'] is False
        user_id, listings = store.member_connected_names.call_args.args
        assert user_id == uuid.UUID(TEST_USER_ID)
        assert set(listings) == {'jira-shared', 'jira', 'confluence'}
        # The shared tokens and the client secret never leave the server.
        assert 'admin-access' not in response.text
        assert 'shh' not in response.text
        auth = items['jira-shared']['server']['auth']
        assert auth['authentication']['client_secret'] == REDACTED_SECRET_VALUE
        assert auth['state']['tokens']['access_token'] == REDACTED_SECRET_VALUE


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

    def test_admin_creates_user_managed_stdio_listing(self, mock_app, org_id):
        store = _store(create=AsyncMock(return_value=None))
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).post(
                f'/api/organizations/{org_id}/mcp-servers', json=_stdio_body()
            )

        assert response.status_code == status.HTTP_201_CREATED
        name, entry = store.create.call_args.args
        assert name == 'slack'
        assert entry.managed_by == 'user'
        assert entry.server.url is None
        assert entry.server.command == 'npx'
        assert entry.server.args == ['-y', '@slack/mcp']
        env = entry.server.env
        assert env['SLACK_BOT_TOKEN'].get_secret_value() == '${SLACK_TOKEN}'

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

    def test_org_managed_oauth_listing_requires_the_admins_authorization(
        self, mock_app, org_id
    ):
        """Every member launches with the shared tokens and a sandbox cannot
        run the consent flow, so an unauthorized listing is refused."""
        store = _store(create=AsyncMock(return_value=None))
        with _patch_role('admin'), _patch_store(store), _patch_job_state() as job_state:
            client = TestClient(mock_app)
            rejected = client.post(
                f'/api/organizations/{org_id}/mcp-servers',
                json={'name': 'jira', 'managed_by': 'org', 'server': _oauth_server()},
            )
            accepted = client.post(
                f'/api/organizations/{org_id}/mcp-servers',
                json={
                    'name': 'jira',
                    'managed_by': 'org',
                    'server': _oauth_server(),
                    'oauth_job_id': 'job-1',
                },
            )

        assert rejected.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert 'authorize' in rejected.json()['detail'].lower()
        assert 'shh' not in rejected.text
        assert accepted.status_code == status.HTTP_201_CREATED
        # The tokens come from the caller's own job, bound to the listing's URL.
        job_state.assert_awaited_once_with('job-1', TEST_USER_ID, OAUTH_URL)
        _name, entry = store.create.call_args.args
        assert entry.managed_by == 'org'
        auth = entry.server.oauth_auth
        assert auth.authentication.client_secret.get_secret_value() == 'shh'
        assert auth.state.tokens.access_token.get_secret_value() == 'admin-access'

    def test_org_managed_oauth_listing_rejects_an_unusable_job(self, mock_app, org_id):
        store = _store(create=AsyncMock(return_value=None))
        refused = HTTPException(
            status.HTTP_404_NOT_FOUND, detail='MCP OAuth job not found'
        )
        with _patch_role('admin'), _patch_store(store), _patch_job_state(error=refused):
            response = TestClient(mock_app).post(
                f'/api/organizations/{org_id}/mcp-servers',
                json={
                    'name': 'jira',
                    'managed_by': 'org',
                    'server': _oauth_server(),
                    'oauth_job_id': 'someone-elses-job',
                },
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        store.create.assert_not_called()

    def test_user_managed_oauth_listing_never_keeps_the_admins_tokens(
        self, mock_app, org_id
    ):
        """Each member authorizes with their own account; whatever state the
        admin's form happened to hold is dropped."""
        store = _store(create=AsyncMock(return_value=None))
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).post(
                f'/api/organizations/{org_id}/mcp-servers',
                json={
                    'name': 'jira',
                    'managed_by': 'user',
                    'server': _oauth_server(OAUTH_STATE),
                },
            )

        assert response.status_code == status.HTTP_201_CREATED
        _name, entry = store.create.call_args.args
        assert entry.managed_by == 'user'
        auth = entry.server.oauth_auth
        assert auth.authentication.client_id == 'cid'
        assert auth.state is None

    @pytest.mark.parametrize(
        'server, fragment',
        [
            ({'transport': 'http'}, 'url'),
            ({'transport': 'stdio'}, 'command'),
            ({}, 'URL (SSE or SHTTP) or a command (stdio)'),
            # A stdio listing takes its credentials through ``env`` only.
            ({'command': 'npx', 'url': DATADOG_URL}, 'do not apply'),
            (
                {'command': 'npx', 'auth': {'strategy': 'bearer', 'value': 'k'}},
                'do not apply',
            ),
            ({'command': 'npx', 'headers': {'X-Key': 'k'}}, 'do not apply'),
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

    def test_redacted_stdio_env_keeps_the_stored_values(self, mock_app, org_id):
        stored = _stored_stdio(managed_by='org', env={'SLACK_BOT_TOKEN': STORED_TOKEN})
        store = _store(
            get=AsyncMock(return_value=stored), update=AsyncMock(return_value=None)
        )
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).put(
                f'/api/organizations/{org_id}/mcp-servers/slack',
                json={
                    'server': {
                        'command': 'npx',
                        'args': ['-y', '@slack/mcp'],
                        'env': {
                            'SLACK_BOT_TOKEN': REDACTED_SECRET_VALUE,
                            'SLACK_TEAM_ID': 'T123',
                        },
                    }
                },
            )

        assert response.status_code == status.HTTP_200_OK
        _name, entry = store.update.call_args.args
        env = entry.server.env
        assert env['SLACK_BOT_TOKEN'].get_secret_value() == STORED_TOKEN
        assert env['SLACK_TEAM_ID'].get_secret_value() == 'T123'

    def test_redacted_stdio_env_is_bound_to_the_command_and_arguments(
        self, mock_app, org_id
    ):
        stored = _stored_stdio(managed_by='org', env={'SLACK_BOT_TOKEN': STORED_TOKEN})
        store = _store(
            get=AsyncMock(return_value=stored), update=AsyncMock(return_value=None)
        )
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).put(
                f'/api/organizations/{org_id}/mcp-servers/slack',
                json={
                    'server': {
                        'command': 'npx',
                        'args': ['-y', '@slack/mcp@2'],
                        'env': {'SLACK_BOT_TOKEN': REDACTED_SECRET_VALUE},
                    }
                },
            )

        # A masked value stands for the stored one only while the process it
        # was entered for is unchanged; otherwise it is dropped, not carried.
        assert response.status_code == status.HTTP_200_OK
        _name, entry = store.update.call_args.args
        assert entry.server.args == ['-y', '@slack/mcp@2']
        assert not entry.server.env

    def test_oauth_credential_without_state_keeps_the_stored_tokens(
        self, mock_app, org_id
    ):
        """The form never sees the tokens: an unchanged edit resends the
        credential with the masked client secret and no ``state``."""
        store = _store(
            get=AsyncMock(return_value=_stored_oauth('org', OAUTH_STATE)),
            update=AsyncMock(return_value=None),
        )
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).put(
                f'/api/organizations/{org_id}/mcp-servers/jira',
                json={
                    'server': {
                        **_oauth_server(client_secret=REDACTED_SECRET_VALUE),
                        'timeout': 45,
                    }
                },
            )

        assert response.status_code == status.HTTP_200_OK
        _name, entry = store.update.call_args.args
        assert entry.server.timeout == 45
        auth = entry.server.oauth_auth
        assert auth.authentication.client_secret.get_secret_value() == 'shh'
        assert auth.state.tokens.access_token.get_secret_value() == 'admin-access'

    def test_oauth_url_change_needs_a_new_authorization(self, mock_app, org_id):
        """A masked credential is restored by URL, so new URL + no state = 422."""
        store = _store(
            get=AsyncMock(return_value=_stored_oauth('org', OAUTH_STATE)),
            update=AsyncMock(return_value=None),
        )
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).put(
                f'/api/organizations/{org_id}/mcp-servers/jira',
                json={
                    'server': {**_oauth_server(), 'url': 'https://other.example/mcp'}
                },
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        store.update.assert_not_called()

    def test_oauth_client_change_needs_a_new_authorization(self, mock_app, org_id):
        """Tokens belong to the client that obtained them: they cannot refresh
        under another one."""
        store = _store(
            get=AsyncMock(return_value=_stored_oauth('org', OAUTH_STATE)),
            update=AsyncMock(return_value=None),
        )
        server = _oauth_server(client_secret=REDACTED_SECRET_VALUE)
        server['auth']['authentication']['client_id'] = 'another-client'
        with _patch_role('admin'), _patch_store(store), _patch_job_state():
            client = TestClient(mock_app)
            rejected = client.put(
                f'/api/organizations/{org_id}/mcp-servers/jira',
                json={'server': server},
            )
            accepted = client.put(
                f'/api/organizations/{org_id}/mcp-servers/jira',
                json={'server': server, 'oauth_job_id': 'job-1'},
            )

        assert rejected.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert accepted.status_code == status.HTTP_200_OK
        store.update.assert_called_once()

    def test_handing_an_oauth_listing_to_users_drops_the_shared_tokens(
        self, mock_app, org_id
    ):
        store = _store(
            get=AsyncMock(return_value=_stored_oauth('org', OAUTH_STATE)),
            update=AsyncMock(return_value=None),
        )
        with _patch_role('admin'), _patch_store(store):
            response = TestClient(mock_app).put(
                f'/api/organizations/{org_id}/mcp-servers/jira',
                json={'managed_by': 'user'},
            )

        assert response.status_code == status.HTTP_200_OK
        _name, entry = store.update.call_args.args
        assert entry.managed_by == 'user'
        assert entry.server.oauth_auth.state is None
        secret = entry.server.oauth_auth.authentication.client_secret
        assert secret.get_secret_value() == 'shh'

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


class TestOrgMcpServerConnection:
    """Any member stores or clears their own OAuth tokens for a user-managed
    OAuth listing; the admin-only permission is not involved."""

    def _store(self, entry: OrgMcpServer | None) -> MagicMock:
        return _store(
            get=AsyncMock(return_value=entry),
            set_member_oauth_state=AsyncMock(return_value=None),
        )

    def _put(self, mock_app, org_id, job_id: str = 'job-1'):
        return TestClient(mock_app).put(
            f'/api/organizations/{org_id}/mcp-servers/jira/connection',
            json={'job_id': job_id},
        )

    def test_non_member_gets_403(self, mock_app, org_id):
        with _patch_role(None):
            response = self._put(mock_app, org_id)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_member_connects_with_the_tokens_of_their_own_job(self, mock_app, org_id):
        store = self._store(_stored_oauth('user', None))
        with (
            _patch_role('member'),
            _patch_store(store),
            _patch_job_state() as job_state,
        ):
            response = self._put(mock_app, org_id)

        assert response.status_code == status.HTTP_200_OK
        job_state.assert_awaited_once_with('job-1', TEST_USER_ID, OAUTH_URL)
        user_id, name, state = store.set_member_oauth_state.call_args.args
        assert user_id == uuid.UUID(TEST_USER_ID)
        assert name == 'jira'
        assert state.tokens.access_token.get_secret_value() == 'admin-access'
        assert state.client_info.model_dump()['client_id'] == 'registered-client'
        assert state.token_expires_at == 1_700_000_000.0
        # The tokens stay server-side.
        assert 'admin-access' not in response.text

    def test_unknown_listing_returns_404(self, mock_app, org_id):
        store = self._store(None)
        with _patch_role('member'), _patch_store(store), _patch_job_state():
            response = self._put(mock_app, org_id)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        store.set_member_oauth_state.assert_not_called()

    @pytest.mark.parametrize(
        'entry',
        [_stored_oauth('org', OAUTH_STATE), _stored_user_managed()],
        ids=['org-managed', 'not-oauth'],
    )
    def test_only_user_managed_oauth_listings_take_a_connection(
        self, mock_app, org_id, entry
    ):
        store = self._store(entry)
        with (
            _patch_role('member'),
            _patch_store(store),
            _patch_job_state() as job_state,
        ):
            response = self._put(mock_app, org_id)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        job_state.assert_not_called()
        store.set_member_oauth_state.assert_not_called()

    def test_unusable_job_is_refused(self, mock_app, org_id):
        store = self._store(_stored_oauth('user', None))
        refused = HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail='did not complete'
        )
        with (
            _patch_role('member'),
            _patch_store(store),
            _patch_job_state(error=refused),
        ):
            response = self._put(mock_app, org_id)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        store.set_member_oauth_state.assert_not_called()

    def test_member_disconnects(self, mock_app, org_id):
        store = _store(clear_member_oauth_state=AsyncMock(return_value=None))
        with _patch_role('member'), _patch_store(store):
            response = TestClient(mock_app).delete(
                f'/api/organizations/{org_id}/mcp-servers/jira/connection'
            )

        assert response.status_code == status.HTTP_200_OK
        store.clear_member_oauth_state.assert_awaited_once_with(
            uuid.UUID(TEST_USER_ID), 'jira'
        )


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
