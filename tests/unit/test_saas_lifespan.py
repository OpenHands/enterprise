"""Tests for SaasAppLifespanService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
