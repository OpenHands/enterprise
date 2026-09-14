"""Recurring native security retention and durable external reconciliation."""

import asyncio

from server.auth.auth_config import ENABLE_KEYCLOAK
from server.auth.bootstrap import verify_auth_installation
from server.logger import logger


async def run_native_maintenance() -> dict[str, int]:
    await verify_auth_installation()
    if ENABLE_KEYCLOAK:
        return {}
    from server.services.admin_user_lifecycle_service import AdminUserLifecycleService
    from server.services.native_auth_service import get_native_auth_service
    from server.services.native_provisioning_service import NativeProvisioningService

    await get_native_auth_service().cleanup_expired_state()
    failures = await AdminUserLifecycleService().retry_native_deletions()
    reconciler = NativeProvisioningService()
    cleaned, cleanup_failed = await reconciler.cleanup()
    provisioned, provisioning_failed = await reconciler.reconcile()
    return {
        'cleaned': cleaned,
        'provisioned': provisioned,
        'error_count': failures + cleanup_failed + provisioning_failed,
    }


async def native_maintenance_loop() -> None:
    while True:
        try:
            result = await run_native_maintenance()
            if result.get('error_count'):
                logger.warning('Native maintenance has pending retries', extra=result)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never emit provider error bodies, which may contain credentials.
            logger.warning('Native maintenance could not complete; retrying')
        await asyncio.sleep(30)
