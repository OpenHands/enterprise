"""Recurring native security retention and durable external reconciliation."""

import asyncio

from server.auth.bootstrap import verify_auth_installation
from server.auth.composition import get_auth_services
from server.logger import logger


async def run_native_maintenance() -> dict[str, int]:
    await verify_auth_installation()
    return await get_auth_services().lifecycle.run_maintenance()


async def native_maintenance_loop() -> None:
    while True:
        try:
            result = await run_native_maintenance()
            if result.get('error_count'):
                logger.warning(
                    'Authentication maintenance has pending retries', extra=result
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never emit provider error bodies, which may contain credentials.
            logger.warning('Authentication maintenance could not complete; retrying')
        await asyncio.sleep(30)
