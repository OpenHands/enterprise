from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from storage.jira_dc_integration_store import JiraDcIntegrationStore
from storage.jira_dc_user import JiraDcUser


def _load_migration_module():
    migration_path = (
        Path(__file__).parents[3]
        / 'migrations'
        / 'versions'
        / '115_enforce_single_active_jira_dc_user_link.py'
    )
    spec = importlib.util.spec_from_file_location(
        'migration_115_enforce_single_active_jira_dc_user_link', migration_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_active_link_migration_uses_postgresql_partial_unique_index():
    migration = _load_migration_module()
    operations = Mock()

    with patch.object(migration, 'op', operations):
        migration.upgrade()

    statement = operations.execute.call_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert 'ROW_NUMBER() OVER' in compiled
    assert 'PARTITION BY keycloak_user_id' in compiled

    args, kwargs = operations.create_index.call_args
    assert args == (
        migration.INDEX_NAME,
        'jira_dc_users',
        ['keycloak_user_id'],
    )
    assert kwargs['unique'] is True
    assert str(kwargs['postgresql_where']) == "status = 'active'"
    assert set(kwargs) == {'unique', 'postgresql_where'}


@pytest.mark.asyncio
async def test_update_user_integration_status_targets_workspace_link():
    store = JiraDcIntegrationStore()
    user = Mock(spec=JiraDcUser)
    user.status = 'inactive'

    result = Mock()
    result.scalar_one_or_none.return_value = user
    session = Mock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    @asynccontextmanager
    async def mock_session_maker():
        yield session

    with patch('storage.jira_dc_integration_store.a_session_maker', mock_session_maker):
        updated_user = await store.update_user_integration_status(
            'user-1', 42, 'active'
        )

    assert updated_user is user
    assert user.status == 'active'
    session.execute.assert_called_once()
    executed_statement = session.execute.call_args.args[0]
    assert 'jira_dc_users.jira_dc_workspace_id' in str(executed_statement)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(user)


@pytest.mark.asyncio
async def test_deactivate_user_links_except_workspace_targets_stale_active_links():
    store = JiraDcIntegrationStore()

    result = Mock()
    result.rowcount = 2
    session = Mock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    @asynccontextmanager
    async def mock_session_maker():
        yield session

    with patch('storage.jira_dc_integration_store.a_session_maker', mock_session_maker):
        deactivated_count = await store.deactivate_user_links_except_workspace(
            'user-1', 42
        )

    assert deactivated_count == 2
    session.execute.assert_called_once()
    executed_statement = session.execute.call_args.args[0]
    statement_text = str(executed_statement)
    assert 'jira_dc_users.keycloak_user_id' in statement_text
    assert 'jira_dc_users.jira_dc_workspace_id !=' in statement_text
    assert 'jira_dc_users.status' in statement_text
    session.commit.assert_awaited_once()


def _session_with_existing(existing):
    """Mock session whose initial (user, workspace) SELECT yields `existing`."""
    result = Mock()
    result.scalar_one_or_none.return_value = existing
    session = Mock()
    session.execute = AsyncMock(return_value=result)
    session.add = Mock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_get_or_create_active_email_link_returns_existing_active():
    store = JiraDcIntegrationStore()
    active = Mock(spec=JiraDcUser)
    active.status = 'active'
    session = _session_with_existing(active)

    @asynccontextmanager
    async def mock_session_maker():
        yield session

    with patch('storage.jira_dc_integration_store.a_session_maker', mock_session_maker):
        result = await store.get_or_create_active_email_link('kc-1', 1)

    assert result is active
    session.add.assert_not_called()
    session.commit.assert_not_awaited()  # already active, nothing written


@pytest.mark.asyncio
async def test_get_or_create_active_email_link_inserts_when_absent():
    store = JiraDcIntegrationStore()
    session = _session_with_existing(None)

    @asynccontextmanager
    async def mock_session_maker():
        yield session

    with patch('storage.jira_dc_integration_store.a_session_maker', mock_session_maker):
        result = await store.get_or_create_active_email_link('kc-1', 7)

    session.add.assert_called_once()
    added = session.add.call_args.args[0]
    assert isinstance(added, JiraDcUser)
    assert added.keycloak_user_id == 'kc-1'
    assert added.jira_dc_workspace_id == 7
    assert added.jira_dc_user_id == 'unavailable'  # email-mode sentinel
    assert added.status == 'active'
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(added)
    assert result is added


@pytest.mark.asyncio
async def test_get_or_create_active_email_link_reactivates_inactive_link():
    """A prior inactive link for the same workspace is reactivated in place, not
    duplicated -- otherwise status-agnostic scalar_one_or_none callers (unlink,
    relink) hit MultipleResultsFound."""
    store = JiraDcIntegrationStore()
    inactive = Mock(spec=JiraDcUser)
    inactive.status = 'inactive'
    session = _session_with_existing(inactive)

    @asynccontextmanager
    async def mock_session_maker():
        yield session

    with patch('storage.jira_dc_integration_store.a_session_maker', mock_session_maker):
        result = await store.get_or_create_active_email_link('kc-1', 1)

    assert inactive.status == 'active'  # reactivated in place
    session.add.assert_not_called()  # no duplicate row
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(inactive)
    assert result is inactive


@pytest.mark.asyncio
async def test_get_or_create_active_email_link_handles_concurrent_insert_race():
    """A concurrent first webhook wins the insert; ours trips the unique index ->
    IntegrityError -> rollback -> re-read returns the winning row."""
    store = JiraDcIntegrationStore()
    winner = Mock(spec=JiraDcUser)
    store.get_active_user_by_keycloak_id_and_workspace = AsyncMock(return_value=winner)

    session = _session_with_existing(None)
    session.commit = AsyncMock(
        side_effect=IntegrityError('insert', {}, Exception('unique violation'))
    )

    @asynccontextmanager
    async def mock_session_maker():
        yield session

    with patch('storage.jira_dc_integration_store.a_session_maker', mock_session_maker):
        result = await store.get_or_create_active_email_link('kc-1', 1)

    session.commit.assert_awaited_once()
    session.rollback.assert_awaited_once()  # failed write rolled back, not cached
    assert result is winner
    store.get_active_user_by_keycloak_id_and_workspace.assert_awaited_once()
