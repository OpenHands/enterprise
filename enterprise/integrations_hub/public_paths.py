from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .config import get_default_config


def public_api_path(internal_path: str) -> str:
    """Map an internal ``/api`` route to its configured public prefix."""
    prefix = get_default_config().api_root_path
    if not prefix or not internal_path.startswith("/api"):
        return internal_path
    if internal_path == prefix or internal_path.startswith(f"{prefix}/"):
        return internal_path
    if internal_path == "/api":
        return prefix
    if not internal_path.startswith("/api/"):
        return internal_path
    return f"{prefix}{internal_path[4:]}"


def internal_api_path(public_path: str) -> str | None:
    """Return the internal route for a configured public API path."""
    prefix = get_default_config().api_root_path
    if not prefix:
        return None
    if public_path == prefix:
        return "/api"
    if public_path.startswith(f"{prefix}/"):
        return f"/api{public_path[len(prefix) :]}"
    return None


def public_ui_path(path: str) -> str:
    """Prefix a same-origin UI path without allowing open redirects."""
    if not path.startswith("/") or path.startswith("//"):
        return path
    prefix = get_default_config().root_path
    if not prefix:
        return path
    if path == prefix or path.startswith(f"{prefix}/"):
        return path
    return f"{prefix}/" if path == "/" else f"{prefix}{path}"


def public_openapi_paths(paths: Mapping[str, Any]) -> dict[str, Any]:
    return {public_api_path(path): operation for path, operation in paths.items()}
