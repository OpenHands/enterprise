"""License enforcement settings, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from openhands.app_server.utils.logger import openhands_logger as logger

DEFAULT_LICENSE_SERVER_URL = 'https://customercenter.openhands.dev'
DEFAULT_CHECK_INTERVAL_SECONDS = 30 * 60
# Customer Center rate-limits each client IP, and every replica checks on its own.
MIN_CHECK_INTERVAL_SECONDS = 60


@dataclass(frozen=True)
class LicenseConfig:
    # When False, OHE makes no license checks and every check helper allows.
    enforcement_enabled: bool
    license_key: str | None
    license_server_url: str
    check_interval_seconds: int

    @classmethod
    def from_env(cls) -> LicenseConfig:
        return cls(
            enforcement_enabled=os.getenv(
                'LICENSE_ENFORCEMENT_ENABLED', 'false'
            ).lower()
            in ('true', '1'),
            license_key=os.getenv('OPENHANDS_LICENSE_KEY', '').strip() or None,
            license_server_url=(
                os.getenv('LICENSE_SERVER_URL', '').strip()
                or DEFAULT_LICENSE_SERVER_URL
            ).rstrip('/'),
            check_interval_seconds=_check_interval_from_env(),
        )


def _check_interval_from_env() -> int:
    raw = os.getenv('LICENSE_CHECK_INTERVAL_SECONDS', '').strip()
    if not raw:
        return DEFAULT_CHECK_INTERVAL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            'license_check_interval_invalid',
            extra={'value': raw, 'default': DEFAULT_CHECK_INTERVAL_SECONDS},
        )
        return DEFAULT_CHECK_INTERVAL_SECONDS
    if value < MIN_CHECK_INTERVAL_SECONDS:
        logger.warning(
            'license_check_interval_too_short',
            extra={'value': value, 'minimum': MIN_CHECK_INTERVAL_SECONDS},
        )
        return MIN_CHECK_INTERVAL_SECONDS
    return value
