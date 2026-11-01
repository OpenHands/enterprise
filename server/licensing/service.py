"""The license check, its in-memory cache, and the helpers that enforce it.

Each server process checks its license key with Customer Center when it
starts, then once every ``LICENSE_CHECK_INTERVAL_SECONDS`` (30 minutes by
default). It keeps the last trusted answer in memory. If a check fails because
Customer Center is unreachable or its answer can't be verified, the process
keeps using the last trusted answer until that answer's ``expires_at``.

Code that enforces the license calls the helpers on ``LicenseService``
(``is_licensed``, ``has_feature``, ``within_limit``) or uses the FastAPI
dependencies ``require_license`` and ``require_feature``. When
``LICENSE_ENFORCEMENT_ENABLED`` is off, every helper allows.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Literal

import httpx
from fastapi import HTTPException, status

from openhands.app_server.utils.logger import openhands_logger as logger
from server.licensing.config import LicenseConfig
from server.licensing.verification import (
    InvalidLicensePayload,
    LicenseFeatures,
    LicenseLimits,
    LicenseVerificationError,
    ValidLicensePayload,
    verify_signed_license,
)

VALIDATE_PATH = '/api/v1/licenses/validate'
REQUEST_TIMEOUT_SECONDS = 10.0

# disabled: enforcement is off.
# missing_key: enforcement is on and OPENHANDS_LICENSE_KEY is not set.
# pending: the first check has not finished.
# valid: the last trusted answer says the license is valid.
# invalid: the last trusted answer says the license is not valid.
# unverified: there is no trusted answer that has not expired.
LicenseState = Literal[
    'disabled', 'missing_key', 'pending', 'valid', 'invalid', 'unverified'
]

LicenseFeature = Literal['sso']
LicenseLimit = Literal['users', 'conversations', 'sandboxes']


@dataclass(frozen=True)
class LicenseStatus:
    state: LicenseState
    payload: ValidLicensePayload | InvalidLicensePayload | None = None
    last_checked_at: datetime | None = None
    last_error: str | None = None

    @property
    def valid_payload(self) -> ValidLicensePayload | None:
        if self.state == 'valid' and isinstance(self.payload, ValidLicensePayload):
            return self.payload
        return None


class LicenseService:
    def __init__(
        self,
        config: LicenseConfig,
        *,
        trusted_public_keys: dict[str, str] | None = None,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.config = config
        self._trusted_public_keys = trusted_public_keys
        self._http_client_factory = http_client_factory or (
            lambda: httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._payload: ValidLicensePayload | InvalidLicensePayload | None = None
        self._checked = False
        self._last_checked_at: datetime | None = None
        self._last_error: str | None = None
        self._task: asyncio.Task[None] | None = None

    # Status

    def status(self) -> LicenseStatus:
        """The license status now, from the cached answer."""
        if not self.config.enforcement_enabled:
            return LicenseStatus(state='disabled')
        if self.config.license_key is None:
            return LicenseStatus(state='missing_key')
        if not self._checked:
            return LicenseStatus(state='pending')

        payload = self._payload
        state: LicenseState
        if payload is None or payload.expires_at <= self._clock():
            state = 'unverified'
            payload = None
        elif payload.valid:
            state = 'valid'
        else:
            state = 'invalid'
        return LicenseStatus(
            state=state,
            payload=payload,
            last_checked_at=self._last_checked_at,
            last_error=self._last_error,
        )

    # Enforcement helpers

    def is_licensed(self) -> bool:
        """Whether this install may run. Pending counts as licensed."""
        state = self.status().state
        return state in ('disabled', 'pending', 'valid')

    def features(self) -> LicenseFeatures | None:
        """The licensed features, or None when enforcement is off."""
        current = self.status()
        if current.state in ('disabled', 'pending'):
            return None
        payload = current.valid_payload
        return payload.features if payload else LicenseFeatures()

    def limits(self) -> LicenseLimits | None:
        """The licensed limits, or None when enforcement is off."""
        current = self.status()
        if current.state in ('disabled', 'pending'):
            return None
        payload = current.valid_payload
        return (
            payload.limits
            if payload
            else LicenseLimits(users=0, conversations=0, sandboxes=0)
        )

    def has_feature(self, feature: LicenseFeature) -> bool:
        features = self.features()
        return True if features is None else bool(getattr(features, feature))

    def within_limit(self, limit: LicenseLimit, count: int) -> bool:
        """Whether ``count`` is within the licensed limit.

        Pass the count the action would reach, e.g. the number of users after
        adding one.
        """
        limits = self.limits()
        if limits is None:
            return True
        maximum = getattr(limits, limit)
        return maximum is None or count <= maximum

    # Checking

    async def check_now(self) -> LicenseStatus:
        """Check the license key with Customer Center and update the cache."""
        license_key = self.config.license_key
        if not self.config.enforcement_enabled or license_key is None:
            return self.status()
        try:
            body = await self._request_validation(license_key)
            payload = verify_signed_license(
                body,
                license_key=license_key,
                now=self._clock(),
                trusted_public_keys=self._trusted_public_keys,
            )
        except (httpx.HTTPError, ValueError, LicenseVerificationError) as e:
            self._last_error = f'{type(e).__name__}: {e}'
            logger.warning(
                'license_check_failed',
                extra={
                    'error': self._last_error,
                    'license_server_url': self.config.license_server_url,
                },
            )
        else:
            self._payload = payload
            self._last_error = None
        self._checked = True
        self._last_checked_at = self._clock()
        current = self.status()
        logger.info(
            'license_check_finished',
            extra={
                'state': current.state,
                'plan': getattr(current.payload, 'plan', None),
                'reason': getattr(current.payload, 'reason', None),
            },
        )
        return current

    async def _request_validation(self, license_key: str) -> object:
        url = f'{self.config.license_server_url}{VALIDATE_PATH}'
        async with self._http_client_factory() as client:
            response = await client.post(url, json={'license_key': license_key})
        response.raise_for_status()
        return response.json()

    # Background loop

    def start(self) -> None:
        """Start checking in the background. Does nothing when enforcement is off."""
        if not self.config.enforcement_enabled or self._task is not None:
            return
        if self.config.license_key is None:
            logger.warning('license_key_missing')
            return
        self._task = asyncio.create_task(self._run(), name='license-check')

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while True:
            try:
                await self.check_now()
            except Exception:
                logger.exception('license_check_crashed')
            await asyncio.sleep(self.config.check_interval_seconds)


_license_service: LicenseService | None = None


def get_license_service() -> LicenseService:
    global _license_service
    if _license_service is None:
        _license_service = LicenseService(LicenseConfig.from_env())
    return _license_service


# FastAPI dependencies


def require_license() -> None:
    """Reject the request with 403 when the install is not licensed."""
    if not get_license_service().is_licensed():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='This OpenHands Enterprise install does not have a valid license.',
        )


def require_feature(feature: LicenseFeature) -> Callable[[], None]:
    """A dependency that rejects the request when the license lacks ``feature``."""

    def dependency() -> None:
        if not get_license_service().has_feature(feature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f'The OpenHands Enterprise license does not include {feature}.',
            )

    return dependency
