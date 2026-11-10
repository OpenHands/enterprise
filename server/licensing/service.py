"""The license check, its in-memory cache, and the helpers that enforce it.

Each server process checks its license key with Customer Center when it
starts, then once every ``LICENSE_CHECK_INTERVAL_SECONDS`` (30 minutes by
default). It keeps the last trusted answer in memory. If a check fails because
Customer Center is unreachable or its answer can't be verified, the process
keeps using the last trusted answer until that answer's ``expires_at``.

The license comes from the environment, or, when the environment sets none,
from the license a Super Admin saved (``load_license_config``). Each process
reads the saved license again before every check and whenever the license
status is read, so a license saved on one replica reaches the others.

With an offline license file (``OPENHANDS_OFFLINE_LICENSE`` or
``OPENHANDS_OFFLINE_LICENSE_FILE``), the process never calls Customer Center
and ignores ``OPENHANDS_LICENSE_KEY``: the file is the license. It reads and
checks the file when it starts and then every hour, so a replaced file takes
effect within an hour. The install is licensed until the file's
``license_ends_at``. After that, the license is invalid with the reason
``expired``, the same reason Customer Center sends. A file whose signature
does not verify, or that can't be read, is invalid with the reason
``invalid_license_file``.

Code that enforces the license calls the helpers on ``LicenseService``
(``is_licensed``, ``has_feature``, ``within_limit``) or uses the FastAPI
dependencies ``require_license`` and ``require_feature``. The checks that
count rows against a limit are in ``limits.py``. When
``LICENSE_ENFORCEMENT_ENABLED`` is off, every helper allows. A license that
is set is still checked, and its status reported, so the license banner shows
it.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Awaitable, Callable, Literal

import httpx
from fastapi import HTTPException, status

from openhands.app_server.utils.logger import openhands_logger as logger
from server.licensing.config import LicenseConfig, load_license_config
from server.licensing.verification import (
    InvalidLicensePayload,
    LicenseFeatures,
    LicenseLimits,
    LicenseVerificationError,
    OfflineLicensePayload,
    ValidLicensePayload,
    read_offline_license,
    verify_signed_license,
)

VALIDATE_PATH = '/api/v1/licenses/validate'
REQUEST_TIMEOUT_SECONDS = 10.0
OFFLINE_CHECK_INTERVAL_SECONDS = 60 * 60

# Reasons an offline license is invalid. A license check's answer carries its
# own reason, and uses the same one for an expired license.
LICENSE_EXPIRED = 'expired'
INVALID_LICENSE_FILE = 'invalid_license_file'

# disabled: enforcement is off, and no license key or offline license is
#   set in the environment or saved by a Super Admin.
# missing_key: enforcement is on, and no license key or offline license is
#   set in the environment or saved by a Super Admin.
# pending: the first check has not finished.
# valid: the last trusted answer says the license is valid.
# invalid: the last trusted answer says the license is not valid, or the
#   offline license has ended or is not a valid file.
# unverified: there is no trusted answer that has not expired.
LicenseState = Literal[
    'disabled', 'missing_key', 'pending', 'valid', 'invalid', 'unverified'
]

LicenseFeature = Literal['sso']
LicenseLimit = Literal['users', 'orgs', 'conversations', 'sandboxes']

# What an install may do without a valid license: one user and one team
# organization, for light use.
UNLICENSED_LIMITS = LicenseLimits(users=1, orgs=1)


@dataclass(frozen=True)
class LicenseStatus:
    state: LicenseState
    payload: ValidLicensePayload | InvalidLicensePayload | None = None
    # Why the license is not valid, when state is invalid.
    reason: str | None = None
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
        config_loader: Callable[[], Awaitable[LicenseConfig]] | None = None,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.config = config
        self._config_loader = config_loader
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
        if self.config.license_key is None and not self.config.offline:
            return LicenseStatus(
                state='missing_key' if self.config.enforcement_enabled else 'disabled'
            )
        if not self._checked:
            return LicenseStatus(state='pending')
        if self.config.offline:
            return self._offline_status()

        payload = self._payload
        state: LicenseState
        reason = None
        if payload is None or payload.expires_at <= self._clock():
            state = 'unverified'
            payload = None
        elif payload.valid:
            state = 'valid'
        else:
            state = 'invalid'
            reason = payload.reason
        return LicenseStatus(
            state=state,
            payload=payload,
            reason=reason,
            last_checked_at=self._last_checked_at,
            last_error=self._last_error,
        )

    def _offline_status(self) -> LicenseStatus:
        payload = self._payload
        state: LicenseState = 'valid'
        reason = None
        if not isinstance(payload, OfflineLicensePayload):
            state, reason = 'invalid', INVALID_LICENSE_FILE
            payload = None
        elif min(payload.license_ends_at, payload.expires_at) <= self._clock():
            # The payload stays, so the admin sees when the license ended.
            state, reason = 'invalid', LICENSE_EXPIRED
        return LicenseStatus(
            state=state,
            payload=payload,
            reason=reason,
            last_checked_at=self._last_checked_at,
            last_error=self._last_error,
        )

    # Enforcement helpers

    def is_licensed(self) -> bool:
        """Whether this install may run. Pending counts as licensed."""
        if not self.config.enforcement_enabled:
            return True
        return self.status().state in ('pending', 'valid')

    def features(self) -> LicenseFeatures | None:
        """The licensed features, or None when enforcement is off."""
        current = self.status()
        if not self.config.enforcement_enabled or current.state == 'pending':
            return None
        payload = current.valid_payload
        return payload.features if payload else LicenseFeatures()

    def limits(self) -> LicenseLimits | None:
        """The licensed limits, or None when enforcement is off.

        Without a valid license, the install gets the limits in
        ``UNLICENSED_LIMITS``.
        """
        current = self.status()
        if not self.config.enforcement_enabled or current.state == 'pending':
            return None
        payload = current.valid_payload
        return payload.limits if payload else UNLICENSED_LIMITS

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

    async def reload(self) -> bool:
        """Read the license settings again, and return whether they changed.

        This picks up a license a Super Admin saved. A changed license is
        pending until the next ``check_now``.
        """
        if self._config_loader is None:
            return False
        config = await self._config_loader()
        if config == self.config:
            return False
        self.config = config
        self._payload = None
        self._checked = False
        self._last_checked_at = None
        self._last_error = None
        return True

    async def check_now(self) -> LicenseStatus:
        """Check the license and update the cache.

        Reads the offline license when one is configured, and otherwise asks
        Customer Center.
        """
        if self.config.offline:
            self._load_offline_license()
        elif self.config.license_key is not None:
            await self._check_online(self.config.license_key)
        else:
            if self.config.enforcement_enabled:
                logger.warning('license_key_missing')
            return self.status()
        self._checked = True
        self._last_checked_at = self._clock()
        current = self.status()
        logger.info(
            'license_check_finished',
            extra={
                'state': current.state,
                'offline': self.config.offline,
                'type': getattr(current.payload, 'type', None),
                'reason': current.reason,
            },
        )
        return current

    async def _check_online(self, license_key: str) -> None:
        try:
            body = await self._request_validation(license_key)
            payload = verify_signed_license(
                body,
                license_key=license_key,
                now=self._clock(),
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

    def _load_offline_license(self) -> None:
        # The file is the only source, so a bad file replaces a good one.
        try:
            self._payload = read_offline_license(self._read_offline_license())
            self._last_error = None
        except (OSError, ValueError, LicenseVerificationError) as e:
            self._payload = None
            self._last_error = f'{type(e).__name__}: {e}'
            logger.warning('offline_license_invalid', extra={'error': self._last_error})

    def _read_offline_license(self) -> object:
        contents, path = self.config.offline_license, self.config.offline_license_file
        if contents is not None and path is not None:
            raise ValueError(
                'Set OPENHANDS_OFFLINE_LICENSE or OPENHANDS_OFFLINE_LICENSE_FILE, not both'
            )
        if path is not None:
            contents = Path(path).read_text(encoding='utf-8')
        if contents is None:
            raise ValueError('No offline license is configured')
        return json.loads(contents)

    async def _request_validation(self, license_key: str) -> object:
        url = f'{self.config.license_server_url}{VALIDATE_PATH}'
        async with self._http_client_factory() as client:
            response = await client.post(url, json={'license_key': license_key})
        response.raise_for_status()
        return response.json()

    # Background loop

    def start(self) -> None:
        """Start checking in the background.

        Does nothing without a license, unless the service can load one: the
        loop then also runs without a license, so that it picks up one a
        Super Admin saves later.
        """
        if self._task is not None:
            return
        if not self.config.configured and self._config_loader is None:
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
                await self.reload()
                await self.check_now()
            except Exception:
                logger.exception('license_check_crashed')
            await asyncio.sleep(
                OFFLINE_CHECK_INTERVAL_SECONDS
                if self.config.offline
                else self.config.check_interval_seconds
            )


_license_service: LicenseService | None = None


def get_license_service() -> LicenseService:
    global _license_service
    if _license_service is None:
        _license_service = LicenseService(
            LicenseConfig.from_env(), config_loader=load_license_config
        )
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
