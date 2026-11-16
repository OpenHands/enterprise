"""License enforcement settings, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from openhands.app_server.utils.logger import openhands_logger as logger
from storage.instance_settings_store import InstanceSettingsStore

DEFAULT_LICENSE_SERVER_URL = 'https://customercenter.openhands.dev'
DEFAULT_CHECK_INTERVAL_SECONDS = 30 * 60
# Customer Center rate-limits each client IP, and every replica checks on its own.
MIN_CHECK_INTERVAL_SECONDS = 60


@dataclass(frozen=True)
class LicenseConfig:
    # When False, every check helper allows. OHE still checks a license that
    # is set, but shows no license status.
    enforcement_enabled: bool
    license_key: str | None
    license_server_url: str
    check_interval_seconds: int
    # An offline license file's contents, for an install that can't reach
    # Customer Center.
    offline_license: str | None = None
    # The path of an offline license file.
    offline_license_file: str | None = None

    @property
    def offline(self) -> bool:
        """Whether an offline license is configured.

        OHE then never calls Customer Center and ignores the license key.
        """
        return self.offline_license is not None or self.offline_license_file is not None

    @property
    def configured(self) -> bool:
        """Whether a license key or an offline license is set."""
        return self.license_key is not None or self.offline

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
            offline_license=os.getenv('OPENHANDS_OFFLINE_LICENSE', '').strip() or None,
            offline_license_file=os.getenv('OPENHANDS_OFFLINE_LICENSE_FILE', '').strip()
            or None,
        )


async def load_license_config() -> LicenseConfig:
    """The license settings in use.

    A license in the environment wins. Without one, the install uses the
    license a Super Admin saved, if any.
    """
    config = LicenseConfig.from_env()
    if config.configured:
        return config
    saved = await InstanceSettingsStore.get_license()
    return replace(
        config, license_key=saved.license_key, offline_license=saved.offline_license
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
