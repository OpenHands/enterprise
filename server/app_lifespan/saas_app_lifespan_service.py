"""SaaS-specific application lifespan service.

Initializes PostHog analytics on startup and flushes buffered events on
clean shutdown so no events are lost when the server exits gracefully.
"""

from __future__ import annotations

import asyncio
import os
import random

from sqlalchemy.exc import DBAPIError, OperationalError

from openhands.analytics import get_analytics_service, init_analytics_service
from openhands.app_server.app_lifespan.app_lifespan_service import AppLifespanService
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.server.types import AppMode
from server.constants import IS_FEATURE_ENV

_ORG_CONDENSER_RECONCILIATION_LOCK_ID = 865115708052677401
_TRANSIENT_SQLSTATES = {
    '40001',  # serialization_failure
    '40P01',  # deadlock_detected
    '55P03',  # lock_not_available / lock timeout
    '57014',  # query_canceled
}
_TRANSIENT_SQLSTATE_PREFIXES = ('08',)  # connection exception class


class TransientReconciliationError(Exception):
    """Retryable org defaults reconciliation failure."""


def _sqlstate(exc: BaseException) -> str | None:
    orig = getattr(exc, 'orig', None)
    for attr in ('sqlstate', 'pgcode'):
        value = getattr(orig, attr, None)
        if isinstance(value, str):
            return value
    return None


def _is_transient_reconciliation_error(exc: BaseException) -> bool:
    if isinstance(exc, OperationalError):
        return True
    if not isinstance(exc, DBAPIError):
        return False
    if exc.connection_invalidated:
        return True
    state = _sqlstate(exc)
    return bool(
        state
        and (
            state in _TRANSIENT_SQLSTATES
            or state.startswith(_TRANSIENT_SQLSTATE_PREFIXES)
        )
    )


class SaasAppLifespanService(AppLifespanService):
    """Lifespan service for the SaaS server.

    On enter: initialises the PostHog analytics singleton from environment vars.
    On exit: calls ``analytics_service.shutdown()`` to flush any buffered events.
    """

    async def __aenter__(self):
        api_key = os.environ.get('POSTHOG_CLIENT_KEY', '')
        host = os.environ.get('POSTHOG_HOST', 'https://us.i.posthog.com')

        init_analytics_service(
            api_key=api_key,
            host=host,
            app_mode=AppMode.SAAS,
            is_feature_env=IS_FEATURE_ENV,
        )
        await self._reconcile_org_condenser_defaults()
        return self

    async def _reconcile_org_condenser_defaults(self) -> None:
        from server.org_defaults_config import get_org_defaults_condenser_config

        config = get_org_defaults_condenser_config()
        if config.max_tokens is None or not config.apply_to_existing:
            return

        max_tokens = config.max_tokens
        try:
            result = await self._reconcile_org_condenser_defaults_once(
                max_tokens=max_tokens,
                overwrite_existing=config.overwrite_existing,
            )
        except TransientReconciliationError:
            await asyncio.sleep(random.uniform(0.25, 1.5))
            try:
                result = await self._reconcile_org_condenser_defaults_once(
                    max_tokens=max_tokens,
                    overwrite_existing=config.overwrite_existing,
                )
            except Exception:
                logger.exception(
                    'org_condenser_defaults_reconciliation_failed',
                    extra={
                        'max_tokens': max_tokens,
                        'apply_to_existing': config.apply_to_existing,
                        'overwrite_existing': config.overwrite_existing,
                    },
                    stack_info=True,
                )
                return
        except Exception:
            logger.exception(
                'org_condenser_defaults_reconciliation_failed',
                extra={
                    'max_tokens': max_tokens,
                    'apply_to_existing': config.apply_to_existing,
                    'overwrite_existing': config.overwrite_existing,
                },
                stack_info=True,
            )
            return

        logger.info(
            'org_condenser_defaults_reconciliation_succeeded',
            extra={
                'max_tokens': max_tokens,
                'apply_to_existing': config.apply_to_existing,
                'overwrite_existing': config.overwrite_existing,
                'updated_count': result.updated_count,
                'skipped_agent_variant_count': result.skipped_agent_variant_count,
                'skipped_condenser_variant_count': result.skipped_condenser_variant_count,
                'malformed_repaired_count': result.malformed_repaired_count,
            },
        )

    async def _reconcile_org_condenser_defaults_once(
        self,
        *,
        max_tokens: int,
        overwrite_existing: bool,
    ):
        from sqlalchemy import text

        from storage.database import a_session_maker
        from storage.org_store import OrgStore

        try:
            async with a_session_maker() as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
                    await session.execute(
                        text('SELECT pg_advisory_xact_lock(:lock_id)'),
                        {'lock_id': _ORG_CONDENSER_RECONCILIATION_LOCK_ID},
                    )
                    return await OrgStore.reconcile_applicable_org_condenser_max_tokens(
                        session,
                        max_tokens=max_tokens,
                        overwrite_existing=overwrite_existing,
                    )
        except Exception as exc:
            if _is_transient_reconciliation_error(exc):
                logger.warning(
                    'org_condenser_defaults_reconciliation_transient_failure',
                    extra={
                        'max_tokens': max_tokens,
                        'overwrite_existing': overwrite_existing,
                        'error': str(exc),
                    },
                )
                raise TransientReconciliationError from exc
            raise

    async def __aexit__(self, exc_type, exc_value, traceback):
        try:
            svc = get_analytics_service()
            if svc is not None:
                svc.shutdown()
        except Exception:
            logger.exception('Error shutting down analytics service', stack_info=True)

        # Release long-lived database resources: the GCP Cloud SQL connector
        # (background cert-refresh tasks + aiohttp ClientSession) and the
        # SQLAlchemy async engine's pool. Without this, every worker respawn
        # leaves the connector's tasks running on the previous event loop.
        try:
            from openhands.app_server.config import get_global_config

            await get_global_config().db_session.close()
        except Exception:
            logger.exception('Error closing DB session injector', stack_info=True)
