"""Tests for SaasAppLifespanService."""

import asyncio
import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests import postgres_testdb


@pytest.fixture
def mock_analytics_service():
    svc = MagicMock()
    svc.shutdown = MagicMock()
    return svc


@pytest.mark.asyncio
async def test_aenter_calls_init_analytics_service():
    """SaasAppLifespanService.__aenter__ initializes the analytics service."""
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    with patch(
        'server.app_lifespan.saas_app_lifespan_service.init_analytics_service'
    ) as mock_init:
        svc = SaasAppLifespanService()
        await svc.__aenter__()
        mock_init.assert_called_once()


@pytest.mark.asyncio
async def test_aenter_runs_org_condenser_reconciliation():
    """Startup must run the org condenser defaults rollout hook."""
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    with (
        patch('server.app_lifespan.saas_app_lifespan_service.init_analytics_service'),
        patch.object(
            SaasAppLifespanService,
            '_reconcile_org_condenser_defaults',
            new_callable=AsyncMock,
        ) as mock_reconcile,
    ):
        svc = SaasAppLifespanService()
        await svc.__aenter__()

    mock_reconcile.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('flag', [None, 'false', '0'])
async def test_aenter_does_not_migrate_unless_enabled(monkeypatch, flag):
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    if flag is None:
        monkeypatch.delenv('RUN_MIGRATIONS_ON_STARTUP', raising=False)
    else:
        monkeypatch.setenv('RUN_MIGRATIONS_ON_STARTUP', flag)

    with (
        patch('server.app_lifespan.saas_app_lifespan_service.init_analytics_service'),
        patch.object(
            SaasAppLifespanService, '_run_migrations', new_callable=AsyncMock
        ) as mock_migrate,
    ):
        await SaasAppLifespanService().__aenter__()

    mock_migrate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('flag', ['true', '1', 'TRUE'])
async def test_aenter_migrates_before_anything_else_when_enabled(monkeypatch, flag):
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    monkeypatch.setenv('RUN_MIGRATIONS_ON_STARTUP', flag)
    order: list[str] = []

    with (
        patch(
            'server.app_lifespan.saas_app_lifespan_service.init_analytics_service',
            side_effect=lambda **_: order.append('analytics'),
        ),
        patch.object(
            SaasAppLifespanService,
            '_run_migrations',
            new=AsyncMock(side_effect=lambda: order.append('migrate')),
        ),
        patch.object(
            SaasAppLifespanService,
            '_reconcile_org_condenser_defaults',
            new=AsyncMock(side_effect=lambda: order.append('reconcile')),
        ),
    ):
        await SaasAppLifespanService().__aenter__()

    assert order == ['migrate', 'analytics', 'reconcile']


@pytest.fixture
def point_migrations_at(monkeypatch, postgres_server):
    """Point alembic at a database on the test server."""

    def _point(database: str) -> None:
        for key in list(os.environ):
            if key.startswith(('DB_', 'GCP_', 'PG')):
                monkeypatch.delenv(key)
        monkeypatch.setenv('DB_HOST', postgres_server.host)
        monkeypatch.setenv('DB_PORT', str(postgres_server.port))
        monkeypatch.setenv('DB_USER', postgres_server.user)
        monkeypatch.setenv('DB_PASS', postgres_server.password)
        monkeypatch.setenv('DB_NAME', database)
        # Migrations branch on these, so pin them as the test template does.
        monkeypatch.setenv('WEB_HOST', '')
        monkeypatch.delenv('STRIPE_API_KEY', raising=False)

    return _point


@pytest.fixture
def empty_database(postgres_server):
    name = postgres_testdb.create_test_database(postgres_server, 'template0')
    try:
        yield name
    finally:
        postgres_testdb.drop_test_database(postgres_server, name)


def _assert_at_head_and_released(postgres_server, database: str) -> None:
    config = Config(str(postgres_testdb.ALEMBIC_INI))
    config.set_main_option('script_location', str(postgres_testdb.MIGRATIONS_DIR))
    heads = set(ScriptDirectory.from_config(config).get_heads())
    engine = create_engine(postgres_server.sync_url(database), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            applied = set(
                conn.execute(text('SELECT version_num FROM alembic_version')).scalars()
            )
            other_connections = conn.execute(
                text(
                    'SELECT count(*) FROM pg_stat_activity '
                    'WHERE datname = current_database() AND pid <> pg_backend_pid()'
                )
            ).scalar()
            lock_free = conn.execute(
                text('SELECT pg_try_advisory_lock(3617572382373537863)')
            ).scalar()
    finally:
        engine.dispose()
    assert applied == heads
    assert other_connections == 0
    assert lock_free


@pytest.mark.asyncio
async def test_run_migrations_brings_an_empty_database_to_head(
    point_migrations_at, empty_database, postgres_server
):
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    point_migrations_at(empty_database)
    root_handlers = list(logging.getLogger().handlers)

    await SaasAppLifespanService()._run_migrations()

    # A connection left open would keep the advisory lock and block other replicas.
    _assert_at_head_and_released(postgres_server, empty_database)
    # alembic.ini's logging config must not replace the app's.
    assert logging.getLogger().handlers == root_handlers
    assert not logging.getLogger('openhands').disabled


@pytest.mark.asyncio
async def test_app_and_another_replica_can_migrate_at_once(
    point_migrations_at, empty_database, postgres_server
):
    """The advisory lock makes one wait for the other, then find head."""
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    point_migrations_at(empty_database)
    other_replica = await asyncio.create_subprocess_exec(
        sys.executable,
        '-m',
        'alembic',
        '-c',
        str(postgres_testdb.ALEMBIC_INI),
        'upgrade',
        'head',
        cwd=postgres_testdb.REPO_ROOT,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    _, returncode = await asyncio.gather(
        SaasAppLifespanService()._run_migrations(), other_replica.wait()
    )

    assert returncode == 0
    _assert_at_head_and_released(postgres_server, empty_database)


@pytest.mark.asyncio
async def test_run_migrations_raises_the_database_error(point_migrations_at):
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    point_migrations_at('no_such_database')

    with pytest.raises(DBAPIError, match='no_such_database'):
        await SaasAppLifespanService()._run_migrations()


@pytest.mark.asyncio
async def test_reconciliation_is_skipped_unless_apply_to_existing(monkeypatch):
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    monkeypatch.setenv('OPENHANDS_ORG_DEFAULTS_CONDENSER_MAX_TOKENS', '200000')
    monkeypatch.delenv(
        'OPENHANDS_ORG_DEFAULTS_CONDENSER_APPLY_TO_EXISTING', raising=False
    )

    with patch.object(
        SaasAppLifespanService,
        '_reconcile_org_condenser_defaults_once',
        new_callable=AsyncMock,
    ) as mock_once:
        await SaasAppLifespanService()._reconcile_org_condenser_defaults()

    mock_once.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciliation_runs_when_apply_to_existing(monkeypatch):
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService
    from storage.org_store import OrgCondenserReconciliationResult

    monkeypatch.setenv('OPENHANDS_ORG_DEFAULTS_CONDENSER_MAX_TOKENS', '200000')
    monkeypatch.setenv('OPENHANDS_ORG_DEFAULTS_CONDENSER_APPLY_TO_EXISTING', 'true')
    monkeypatch.delenv(
        'OPENHANDS_ORG_DEFAULTS_CONDENSER_OVERWRITE_EXISTING', raising=False
    )

    with patch.object(
        SaasAppLifespanService,
        '_reconcile_org_condenser_defaults_once',
        new_callable=AsyncMock,
        return_value=OrgCondenserReconciliationResult(1, 0, 0, 0),
    ) as mock_once:
        await SaasAppLifespanService()._reconcile_org_condenser_defaults()

    mock_once.assert_awaited_once_with(max_tokens=200000, overwrite_existing=False)


@pytest.mark.asyncio
async def test_aenter_passes_env_vars_to_init():
    """SaasAppLifespanService reads config from env vars."""
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    with (
        patch(
            'server.app_lifespan.saas_app_lifespan_service.init_analytics_service'
        ) as mock_init,
        patch(
            'server.app_lifespan.saas_app_lifespan_service.DEPLOYMENT_MODE',
            'cloud',
        ),
        patch.dict(
            'os.environ',
            {
                'POSTHOG_CLIENT_KEY': 'test-key',
                'POSTHOG_HOST': 'https://test.posthog.com',
                'OPENHANDS_CONFIG_CLS': 'server.config.SaaSServerConfig',
            },
        ),
    ):
        svc = SaasAppLifespanService()
        await svc.__aenter__()

        call_kwargs = mock_init.call_args
        assert call_kwargs.kwargs['api_key'] == 'test-key'
        assert call_kwargs.kwargs['host'] == 'https://test.posthog.com'
        assert call_kwargs.kwargs['deployment_kind'] == 'remote'


@pytest.mark.asyncio
async def test_aenter_disables_analytics_when_self_hosted():
    """Self-hosted Enterprise ignores any configured PostHog key."""
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    with (
        patch(
            'server.app_lifespan.saas_app_lifespan_service.init_analytics_service'
        ) as mock_init,
        patch(
            'server.app_lifespan.saas_app_lifespan_service.DEPLOYMENT_MODE',
            'self_hosted',
        ),
        patch.dict('os.environ', {'POSTHOG_CLIENT_KEY': 'configured-posthog-key'}),
    ):
        svc = SaasAppLifespanService()
        await svc.__aenter__()

        assert mock_init.call_args.kwargs['api_key'] == ''
        assert mock_init.call_args.kwargs['deployment_kind'] == 'local'


@pytest.mark.asyncio
async def test_transient_failure_is_retried_once_then_succeeds(monkeypatch):
    from server.app_lifespan.saas_app_lifespan_service import (
        SaasAppLifespanService,
        TransientReconciliationError,
    )
    from storage.org_store import OrgCondenserReconciliationResult

    monkeypatch.setenv('OPENHANDS_ORG_DEFAULTS_CONDENSER_MAX_TOKENS', '200000')
    monkeypatch.setenv('OPENHANDS_ORG_DEFAULTS_CONDENSER_APPLY_TO_EXISTING', 'true')

    success = OrgCondenserReconciliationResult(1, 0, 0, 0)
    with (
        patch(
            'server.app_lifespan.saas_app_lifespan_service.asyncio.sleep',
            new_callable=AsyncMock,
        ) as mock_sleep,
        patch.object(
            SaasAppLifespanService,
            '_reconcile_org_condenser_defaults_once',
            new_callable=AsyncMock,
            side_effect=[TransientReconciliationError(), success],
        ) as mock_once,
    ):
        await SaasAppLifespanService()._reconcile_org_condenser_defaults()

    assert mock_once.await_count == 2
    mock_sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_transient_failure_is_not_retried(monkeypatch):
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    monkeypatch.setenv('OPENHANDS_ORG_DEFAULTS_CONDENSER_MAX_TOKENS', '200000')
    monkeypatch.setenv('OPENHANDS_ORG_DEFAULTS_CONDENSER_APPLY_TO_EXISTING', 'true')

    with patch.object(
        SaasAppLifespanService,
        '_reconcile_org_condenser_defaults_once',
        new_callable=AsyncMock,
        side_effect=RuntimeError('boom'),
    ) as mock_once:
        await SaasAppLifespanService()._reconcile_org_condenser_defaults()

    assert mock_once.await_count == 1


def test_operational_error_is_classified_transient():
    from sqlalchemy.exc import DBAPIError, OperationalError

    from server.app_lifespan.saas_app_lifespan_service import (
        _is_transient_reconciliation_error,
    )

    class _PgError(Exception):
        def __init__(self, sqlstate: str | None = None, pgcode: str | None = None):
            super().__init__('database error')
            if sqlstate is not None:
                self.sqlstate = sqlstate
            if pgcode is not None:
                self.pgcode = pgcode

    def db_error(orig: BaseException, *, connection_invalidated: bool = False):
        return DBAPIError(
            'SELECT 1', {}, orig, connection_invalidated=connection_invalidated
        )

    assert _is_transient_reconciliation_error(
        OperationalError('SELECT 1', {}, Exception('conn reset'))
    )
    assert _is_transient_reconciliation_error(
        db_error(Exception('conn reset'), connection_invalidated=True)
    )
    for sqlstate in ('40001', '40P01', '55P03', '57014', '08006'):
        assert _is_transient_reconciliation_error(db_error(_PgError(sqlstate)))
    assert _is_transient_reconciliation_error(db_error(_PgError(pgcode='40P01')))
    assert not _is_transient_reconciliation_error(db_error(Exception('syntax error')))


@pytest.mark.asyncio
async def test_reconciliation_takes_advisory_lock_with_timeout():
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    executed = []

    class _TransactionContext:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *args):
            return False

    class _Session:
        def begin(self):
            return _TransactionContext()

        async def execute(self, statement, params=None):
            executed.append((str(statement), params))

    session = _Session()

    class _SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return False

    with (
        patch('storage.database.a_session_maker', lambda: _SessionContext()),
        patch(
            'storage.org_store.OrgStore.reconcile_applicable_org_condenser_max_tokens',
            new_callable=AsyncMock,
        ),
    ):
        await SaasAppLifespanService()._reconcile_org_condenser_defaults_once(
            max_tokens=200000,
            overwrite_existing=False,
        )

    assert any('lock_timeout' in sql for sql, _ in executed)
    assert any('pg_advisory_xact_lock' in sql for sql, _ in executed)


@pytest.mark.asyncio
async def test_aexit_calls_shutdown_when_service_exists(mock_analytics_service):
    """SaasAppLifespanService.__aexit__ calls shutdown on the analytics service."""
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    with (
        patch('server.app_lifespan.saas_app_lifespan_service.init_analytics_service'),
        patch(
            'server.app_lifespan.saas_app_lifespan_service.get_analytics_service',
            return_value=mock_analytics_service,
        ),
    ):
        svc = SaasAppLifespanService()
        await svc.__aenter__()
        await svc.__aexit__(None, None, None)

        mock_analytics_service.shutdown.assert_called_once()


@pytest.mark.asyncio
async def test_aexit_does_not_raise_when_service_is_none():
    """SaasAppLifespanService.__aexit__ does not raise if analytics service is None."""
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    with (
        patch('server.app_lifespan.saas_app_lifespan_service.init_analytics_service'),
        patch(
            'server.app_lifespan.saas_app_lifespan_service.get_analytics_service',
            return_value=None,
        ),
    ):
        svc = SaasAppLifespanService()
        await svc.__aenter__()
        # Must not raise
        await svc.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_aexit_does_not_raise_on_shutdown_error(mock_analytics_service):
    """SaasAppLifespanService.__aexit__ swallows errors from shutdown."""
    from server.app_lifespan.saas_app_lifespan_service import SaasAppLifespanService

    mock_analytics_service.shutdown.side_effect = RuntimeError('connection closed')

    with (
        patch('server.app_lifespan.saas_app_lifespan_service.init_analytics_service'),
        patch(
            'server.app_lifespan.saas_app_lifespan_service.get_analytics_service',
            return_value=mock_analytics_service,
        ),
    ):
        svc = SaasAppLifespanService()
        await svc.__aenter__()
        # Must not raise even if shutdown errors
        await svc.__aexit__(None, None, None)
