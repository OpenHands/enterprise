"""Read canonical configuration names while accepting their legacy aliases."""

import os
from typing import overload


@overload
def get_config_env(name: str, legacy_name: str, default: str) -> str: ...


@overload
def get_config_env(name: str, legacy_name: str, default: None = None) -> str | None: ...


def get_config_env(
    name: str, legacy_name: str, default: str | None = None
) -> str | None:
    """Prefer the canonical value, including empty values, over the legacy alias."""
    value = os.getenv(name)
    if value is not None:
        return value
    return os.getenv(legacy_name, default)
