"""
Tests for JiraIntegrationStore async methods.

The store uses async database sessions (a_session_maker) for all operations,
which is critical for avoiding asyncpg event loop issues when called from
FastAPI async endpoints.
"""

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from storage.jira_integration_store import (
    JiraIntegrationStore,
    workspace_visible_to_org,
)
from storage.jira_user import JiraUser
from storage.jira_workspace import JiraWorkspace


@pytest.fixture
def store():
    """Create a JiraIntegrationStore instance."""
    return JiraIntegrationStore()


@pytest.fixture
def create_mock_async_session():
    """Factory to create properly mocked async session context manager."""

    def _create(query_result=None, all_results=None):
        mock_session = Mock()
        mock_result = Mock()

        if all_results is not None:
            mock_result.scalars.return_value.all.return_value = all_results
        else:
            mock_result.scalars.return_value.first.return_value = query_result

        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = Mock()
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        @asynccontextmanager
        async def mock_context_manager():
            yield mock_session

        return mock_context_manager, mock_session

    return _create


class TestJiraIntegrationStoreAsyncMethods:
    """Tests verifying JiraIntegrationStore methods use async sessions correctly."""

    @pytest.mark.asyncio
    async def test_get_workspace_by_id_returns_workspace(
        self, store, create_mock_async_session
    ):
        """Test get_workspace_by_id returns workspace when found."""
        # Arrange
        mock_workspace = Mock(spec=JiraWorkspace)
        mock_workspace.id = 1
        mock_workspace.name = 'test-workspace'

        mock_context_manager, mock_session = create_mock_async_session(mock_workspace)

        # Act
        with patch(
            'storage.jira_integration_store.a_session_maker', mock_context_manager
        ):
            result = await store.get_workspace_by_id(1)

        # Assert
        assert result == mock_workspace
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_workspace_by_id_returns_none_when_not_found(
        self, store, create_mock_async_session
    ):
        """Test get_workspace_by_id returns None when workspace not found."""
        # Arrange
        mock_context_manager, mock_session = create_mock_async_session(None)

        # Act
        with patch(
            'storage.jira_integration_store.a_session_maker', mock_context_manager
        ):
            result = await store.get_workspace_by_id(999)

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_get_workspace_by_name_normalizes_to_lowercase(
        self, store, create_mock_async_session
    ):
        """Test get_workspace_by_name converts name to lowercase for query."""
        # Arrange
        mock_workspace = Mock(spec=JiraWorkspace)
        mock_workspace.name = 'test-workspace'

        mock_context_manager, mock_session = create_mock_async_session(mock_workspace)

        # Act
        with patch(
            'storage.jira_integration_store.a_session_maker', mock_context_manager
        ):
            result = await store.get_workspace_by_name('TEST-WORKSPACE')

        # Assert
        assert result == mock_workspace
        # Verify the query was executed (filter includes lowercase conversion)
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_active_user_filters_by_status(
        self, store, create_mock_async_session
    ):
        """Test get_active_user only returns users with active status."""
        # Arrange
        mock_user = Mock(spec=JiraUser)
        mock_user.jira_user_id = 'jira-123'
        mock_user.jira_workspace_id = 1
        mock_user.status = 'active'

        mock_context_manager, mock_session = create_mock_async_session(mock_user)

        # Act
        with patch(
            'storage.jira_integration_store.a_session_maker', mock_context_manager
        ):
            result = await store.get_active_user('jira-123', 1)

        # Assert
        assert result == mock_user
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_workspace_adds_and_commits(
        self, store, create_mock_async_session
    ):
        """Test create_workspace properly adds, commits, and refreshes."""
        # Arrange
        mock_context_manager, mock_session = create_mock_async_session(None)

        # Act
        with patch(
            'storage.jira_integration_store.a_session_maker', mock_context_manager
        ):
            await store.create_workspace(
                name='TEST-WORKSPACE',
                jira_cloud_id='cloud-123',
                admin_user_id='admin-user',
                org_id=None,
                encrypted_webhook_secret='encrypted-secret',
                svc_acc_email='svc@test.com',
                encrypted_svc_acc_api_key='encrypted-key',
                status='active',
            )

        # Assert
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once()

        # Verify workspace was created with lowercase name
        added_workspace = mock_session.add.call_args[0][0]
        assert added_workspace.name == 'test-workspace'

    @pytest.mark.asyncio
    async def test_update_user_integration_status_raises_if_not_found(
        self, store, create_mock_async_session
    ):
        """Test update_user_integration_status raises ValueError if user not found."""
        # Arrange
        mock_context_manager, mock_session = create_mock_async_session(None)

        # Act & Assert
        with patch(
            'storage.jira_integration_store.a_session_maker', mock_context_manager
        ):
            with pytest.raises(ValueError) as exc_info:
                await store.update_user_integration_status('unknown-user', 'inactive')

            assert 'Jira user not found' in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_deactivate_workspace_deactivates_all_users(
        self, store, create_mock_async_session
    ):
        """Test deactivate_workspace sets all users and workspace to inactive."""
        # Arrange
        mock_user1 = Mock(spec=JiraUser)
        mock_user1.status = 'active'
        mock_user2 = Mock(spec=JiraUser)
        mock_user2.status = 'active'

        mock_workspace = Mock(spec=JiraWorkspace)
        mock_workspace.status = 'active'

        mock_session = Mock()

        # First execute returns users, second returns workspace
        call_count = [0]

        def execute_side_effect(*args, **kwargs):
            result = Mock()
            if call_count[0] == 0:
                result.scalars.return_value.all.return_value = [mock_user1, mock_user2]
            else:
                result.scalars.return_value.first.return_value = mock_workspace
            call_count[0] += 1
            return result

        mock_session.execute = AsyncMock(side_effect=execute_side_effect)
        mock_session.add = Mock()
        mock_session.commit = AsyncMock()

        @asynccontextmanager
        async def mock_context_manager():
            yield mock_session

        # Act
        with patch(
            'storage.jira_integration_store.a_session_maker', mock_context_manager
        ):
            await store.deactivate_workspace(1)

        # Assert
        assert mock_user1.status == 'inactive'
        assert mock_user2.status == 'inactive'
        assert mock_workspace.status == 'inactive'
        mock_session.commit.assert_called_once()


def _workspace(org_id=None, admin_user_id='admin-1', status='active'):
    workspace = Mock(spec=JiraWorkspace)
    workspace.org_id = org_id
    workspace.admin_user_id = admin_user_id
    workspace.status = status
    return workspace


class TestWorkspaceVisibleToOrg:
    """Tests for the org-visibility predicate used by the status endpoint."""

    def test_workspace_without_org_is_install_wide(self):
        assert workspace_visible_to_org(_workspace(org_id=None), uuid4()) is True

    def test_personal_org_stamp_is_install_wide(self):
        """org_id == admin_user_id means the creator's personal org (org.id ==
        user.id), which must behave like the legacy unscoped rows."""
        admin_id = uuid4()
        workspace = _workspace(org_id=admin_id, admin_user_id=str(admin_id))
        assert workspace_visible_to_org(workspace, uuid4()) is True

    def test_matching_org_is_visible(self):
        org_id = uuid4()
        assert workspace_visible_to_org(_workspace(org_id=org_id), org_id) is True

    def test_other_org_is_not_visible(self):
        workspace = _workspace(org_id=uuid4())
        assert workspace_visible_to_org(workspace, uuid4()) is False
        assert workspace_visible_to_org(workspace, None) is False


class TestGetOrCreateActiveEmailLink:
    """Tests for email-match auto-enrollment."""

    @pytest.mark.asyncio
    async def test_returns_existing_active_link_without_writing(
        self, store, create_mock_async_session
    ):
        # Arrange
        active_user = Mock(spec=JiraUser)
        active_user.status = 'active'
        mock_context_manager, mock_session = create_mock_async_session(active_user)

        # Act
        with patch(
            'storage.jira_integration_store.a_session_maker', mock_context_manager
        ):
            result = await store.get_or_create_active_email_link('kc-1', 1)

        # Assert
        assert result is active_user
        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inserts_sentinel_link_when_absent(
        self, store, create_mock_async_session
    ):
        # Arrange
        mock_context_manager, mock_session = create_mock_async_session(None)

        # Act
        with patch(
            'storage.jira_integration_store.a_session_maker', mock_context_manager
        ):
            result = await store.get_or_create_active_email_link('kc-1', 7)

        # Assert
        mock_session.add.assert_called_once()
        added = mock_session.add.call_args.args[0]
        assert isinstance(added, JiraUser)
        assert added.keycloak_user_id == 'kc-1'
        assert added.jira_workspace_id == 7
        assert added.jira_user_id == 'unavailable'  # email-mode sentinel
        assert added.status == 'active'
        mock_session.commit.assert_awaited_once()
        assert result is added

    @pytest.mark.asyncio
    async def test_reactivates_inactive_link_in_place(
        self, store, create_mock_async_session
    ):
        """A prior inactive link for the same workspace is reactivated, not
        duplicated -- callers treat the (user, workspace) pair as unique."""
        # Arrange
        inactive_user = Mock(spec=JiraUser)
        inactive_user.status = 'inactive'
        mock_context_manager, mock_session = create_mock_async_session(inactive_user)

        # Act
        with patch(
            'storage.jira_integration_store.a_session_maker', mock_context_manager
        ):
            result = await store.get_or_create_active_email_link('kc-1', 1)

        # Assert
        assert inactive_user.status == 'active'
        mock_session.add.assert_not_called()
        mock_session.commit.assert_awaited_once()
        assert result is inactive_user

    @pytest.mark.asyncio
    async def test_concurrent_insert_race_rolls_back_and_returns_winner(
        self, store, create_mock_async_session
    ):
        """A concurrent first webhook wins the insert; ours trips the partial
        unique index -> IntegrityError -> rollback -> re-read the winner."""
        # Arrange
        winner = Mock(spec=JiraUser)
        store.get_active_user_by_keycloak_id_and_workspace = AsyncMock(
            return_value=winner
        )
        mock_context_manager, mock_session = create_mock_async_session(None)
        mock_session.commit = AsyncMock(
            side_effect=IntegrityError('insert', {}, Exception('unique violation'))
        )
        mock_session.rollback = AsyncMock()

        # Act
        with patch(
            'storage.jira_integration_store.a_session_maker', mock_context_manager
        ):
            result = await store.get_or_create_active_email_link('kc-1', 1)

        # Assert
        mock_session.rollback.assert_awaited_once()
        assert result is winner
        store.get_active_user_by_keycloak_id_and_workspace.assert_awaited_once_with(
            'kc-1', 1
        )


class TestGetActiveWorkspaceForOrg:
    """Tests for the org-filtered active-workspace lookup."""

    @pytest.mark.asyncio
    async def test_returns_first_workspace_visible_to_org(
        self, store, create_mock_async_session
    ):
        # Arrange
        org_id = uuid4()
        other_org_workspace = _workspace(org_id=uuid4())
        own_org_workspace = _workspace(org_id=org_id)
        mock_context_manager, _ = create_mock_async_session(
            all_results=[other_org_workspace, own_org_workspace]
        )

        # Act
        with patch(
            'storage.jira_integration_store.a_session_maker', mock_context_manager
        ):
            result = await store.get_active_workspace_for_org(org_id)

        # Assert
        assert result is own_org_workspace

    @pytest.mark.asyncio
    async def test_returns_none_when_no_workspace_is_visible(
        self, store, create_mock_async_session
    ):
        # Arrange
        mock_context_manager, _ = create_mock_async_session(
            all_results=[_workspace(org_id=uuid4())]
        )

        # Act
        with patch(
            'storage.jira_integration_store.a_session_maker', mock_context_manager
        ):
            result = await store.get_active_workspace_for_org(uuid4())

        # Assert
        assert result is None


def test_active_link_migration_uses_postgresql_partial_unique_index():
    """Migration 147 dedupes multi-active rows and creates the partial unique
    index that makes email-mode auto-enroll race-safe."""
    migration_path = (
        Path(__file__).parents[3]
        / 'migrations'
        / 'versions'
        / '147_enforce_single_active_jira_user_link.py'
    )
    spec = importlib.util.spec_from_file_location(
        'migration_147_enforce_single_active_jira_user_link', migration_path
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    operations = Mock()
    with patch.object(migration, 'op', operations):
        migration.upgrade()

    statement = str(operations.execute.call_args.args[0])
    assert 'ROW_NUMBER() OVER' in statement
    assert 'PARTITION BY keycloak_user_id' in statement
    assert 'jira_users' in statement

    args, kwargs = operations.create_index.call_args
    assert args == (migration.INDEX_NAME, 'jira_users', ['keycloak_user_id'])
    assert kwargs['unique'] is True
    assert str(kwargs['postgresql_where']) == "status = 'active'"
