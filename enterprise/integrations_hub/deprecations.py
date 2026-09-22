from __future__ import annotations

import warnings


def warn_deprecated(
    feature: str,
    *,
    deprecated_in: str,
    removed_in: str,
    replacement: str,
    stacklevel: int = 2,
) -> None:
    """Warn when a scheduled compatibility path is exercised at runtime."""
    warnings.warn(
        f"{feature} is deprecated since {deprecated_in} and will be removed in "
        f"{removed_in}; use {replacement} instead.",
        DeprecationWarning,
        stacklevel=stacklevel,
    )
