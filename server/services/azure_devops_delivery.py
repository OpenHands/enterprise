"""Verify Azure-to-OpenHands delivery without dispatching sample events."""

import asyncio
import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from integrations.azure_devops.azure_devops_service import SaaSAzureDevOpsService
from openhands.app_server.integrations.azure_devops.service.base import (
    AzureDevOpsApiError,
)
from openhands.app_server.utils.logger import openhands_logger as logger
from storage.azure_devops_webhook_diagnostics import save_webhook_diagnostics
from storage.redis import get_redis_client_async

DELIVERY_TIMEOUT = 20
PROBE_TTL = 90


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeliveryCheck(BaseModel):
    subscription_id: str
    event_type: str
    notification_id: int | None = None
    received_at: str | None = None
    error_code: str | None = None
    azure_http_status: int | None = None
    callback_http_status: int | None = None
    azure_result: str | None = None


class DeliveryTestResult(BaseModel):
    test_id: str = Field(default_factory=lambda: str(uuid4()))
    outcome: Literal['pending', 'verified', 'failed'] = 'pending'
    started_at: str = Field(default_factory=now)
    finished_at: str | None = None
    error: str | None = None
    checks: list[DeliveryCheck] = Field(default_factory=list)


def configuration_fingerprint(subscriptions: list[dict[str, Any]], secret: str) -> str:
    config = sorted(subscriptions, key=lambda hook: str(hook.get('id')))
    fields = ('id', 'status', 'eventType', 'publisherInputs', 'consumerInputs')
    serialized = json.dumps(
        [{key: hook.get(key) for key in fields} for hook in config], sort_keys=True
    )
    return hashlib.sha256((secret + serialized).encode()).hexdigest()


async def save_delivery_result(
    organization: str, result: DeliveryTestResult, fingerprint: str | None, user_id: str
) -> None:
    try:
        await save_webhook_diagnostics(
            organization,
            last_delivery_test={
                **result.model_dump(),
                'configuration_fingerprint': fingerprint,
                'user_id': user_id,
            },
        )
    except Exception:
        logger.exception('Could not save Azure DevOps delivery test')


async def receive_delivery_probe(token: str) -> bool:
    if len(token) != 64 or any(char not in '0123456789abcdef' for char in token):
        return False
    redis = get_redis_client_async()
    return bool(
        await redis.set(f'azure_devops_probe:{token}', now(), xx=True, keepttl=True)
    )


async def verify_delivery(
    service: SaaSAzureDevOpsService,
    subscriptions: list[dict[str, Any]],
    secret: str,
    user_id: str,
) -> DeliveryTestResult:
    result = DeliveryTestResult(
        checks=[
            DeliveryCheck(subscription_id=str(hook['id']), event_type=hook['eventType'])
            for hook in subscriptions
        ]
    )
    fingerprint = configuration_fingerprint(subscriptions, secret)
    redis = get_redis_client_async()
    lock = redis.lock(
        f'azure_devops_delivery_lock:{service.organization.casefold()}',
        timeout=PROBE_TTL,
    )
    if not await lock.acquire(blocking=False):
        result.outcome = 'failed'
        result.error = 'A delivery test is already running. Try again shortly.'
        result.finished_at = now()
        return result

    async def check(hook: dict[str, Any], evidence: DeliveryCheck) -> None:
        token = secrets.token_hex(32)
        key = f'azure_devops_probe:{token}'
        try:
            await redis.set(key, 'pending', ex=PROBE_TTL)
            notification = await service.create_service_hook_test_notification(
                hook, token
            )
            notification_id = notification.get('id')
            if isinstance(notification_id, int):
                evidence.notification_id = notification_id
            while True:
                receipt = await redis.get(key)
                if receipt and receipt not in (b'pending', 'pending'):
                    evidence.received_at = (
                        receipt.decode() if isinstance(receipt, bytes) else receipt
                    )
                    return
                await asyncio.sleep(0.25)
        except AzureDevOpsApiError as exc:
            evidence.error_code = 'azure_api_rejected'
            evidence.azure_http_status = exc.status_code
        except asyncio.CancelledError:
            evidence.error_code = 'callback_not_received'
            raise
        except Exception:
            evidence.error_code = 'test_unavailable'
        finally:
            try:
                await redis.delete(key)
            except Exception:
                logger.warning(
                    'Could not remove Azure DevOps delivery probe; it will expire'
                )

    try:
        await save_delivery_result(service.organization, result, fingerprint, user_id)
        try:
            async with asyncio.timeout(DELIVERY_TIMEOUT):
                await asyncio.gather(
                    *(
                        check(hook, item)
                        for hook, item in zip(subscriptions, result.checks, strict=True)
                    )
                )
        except TimeoutError:
            pass

        async def failure_details(evidence: DeliveryCheck) -> None:
            if evidence.received_at or evidence.notification_id is None:
                return
            try:
                async with asyncio.timeout(3):
                    notification = await service.get_service_hook_test_notification(
                        evidence.notification_id
                    )
                result_value = notification.get('result')
                if result_value in ('pending', 'succeeded', 'failed', 'filtered'):
                    evidence.azure_result = result_value
                response = (notification.get('details') or {}).get('response') or ''
                match = re.search(r'Status Code: (\d{3})', response)
                if match:
                    evidence.callback_http_status = int(match.group(1))
            except Exception:
                pass

        await asyncio.gather(*(failure_details(item) for item in result.checks))
        result.outcome = (
            'verified'
            if result.checks and all(item.received_at for item in result.checks)
            else 'failed'
        )
        if result.outcome == 'failed':
            result.error = (
                'Delivery could not be verified. Check Azure DevOps Service Hook permissions '
                'and that Azure DevOps can reach this OpenHands instance, then test again.'
            )
        return result
    finally:
        if result.outcome == 'pending':
            result.outcome = 'failed'
            result.error = 'The delivery test was interrupted. Test again.'
        result.finished_at = now()
        await save_delivery_result(service.organization, result, fingerprint, user_id)
        try:
            await lock.release()
        except Exception:
            logger.warning(
                'Could not release Azure DevOps delivery test lock; it will expire'
            )
