"""Deployment-configured organization defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass

_BOOLEAN_ENV_VALUES = {
    'true': True,
    '1': True,
    'false': False,
    '0': False,
}


@dataclass(frozen=True)
class OrgDefaultsCondenserConfig:
    """Deployment defaults for organization condenser settings."""

    max_tokens: int | None
    apply_to_existing: bool
    overwrite_existing: bool


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        return default
    normalized = raw.strip().lower()
    if normalized not in _BOOLEAN_ENV_VALUES:
        raise ValueError(f'{name} must be one of true, false, 1, or 0; got {raw!r}')
    return _BOOLEAN_ENV_VALUES[normalized]


def _parse_positive_int_env(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        return None
    value = raw.strip()
    if not value.isdecimal():
        raise ValueError(f'{name} must be a positive base-10 integer; got {raw!r}')
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f'{name} must be greater than 0; got {raw!r}')
    return parsed


def get_org_defaults_condenser_config() -> OrgDefaultsCondenserConfig:
    """Read Helm/Replicated-controlled organization condenser defaults."""

    return OrgDefaultsCondenserConfig(
        max_tokens=_parse_positive_int_env(
            'OPENHANDS_ORG_DEFAULTS_CONDENSER_MAX_TOKENS'
        ),
        apply_to_existing=_parse_bool_env(
            'OPENHANDS_ORG_DEFAULTS_CONDENSER_APPLY_TO_EXISTING', default=False
        ),
        overwrite_existing=_parse_bool_env(
            'OPENHANDS_ORG_DEFAULTS_CONDENSER_OVERWRITE_EXISTING', default=False
        ),
    )
