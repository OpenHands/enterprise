from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from .config import get_default_config


_AUTH_CACHE_TTL_SECONDS = 20.0
_AUTH_UNAVAILABLE_CACHE_TTL_SECONDS = 2.0
_AUTH_CACHE_MAX_ENTRIES = 1024
_AUTH_CACHE: dict[str, tuple[float, OpenHandsUser | None | object]] = {}
_MISS = object()
_UNAVAILABLE = object()
_logger = logging.getLogger("integrations_hub.auth")


class AuthUnavailable(Exception):
    """OpenHands Cloud could not be reached or returned a non-auth error.

    Raised (and surfaced as HTTP 503) when a session check fails for reasons
    other than an invalid token — e.g. network blip, DNS not ready, or a 5xx
    from OpenHands Cloud. Distinct from "token invalid" (401/403) so the
    dashboard treats transient failures as retryable instead of forcing
    a trip through login.
    """


NULL_ORG_SCOPE = "null"
_ADMIN_ROLES = frozenset({"owner", "admin"})


@dataclass(frozen=True)
class OpenHandsUser:
    id: str
    org_id: str | None
    email: str
    role: str
    permissions: tuple[str, ...]
    org_name: str | None = None

    @property
    def normalized_org_id(self) -> str | None:
        return normalize_org_id(self.id, self.org_id)

    @property
    def owner_id(self) -> str:
        return owner_scope_id(self.id, self.normalized_org_id)

    @property
    def is_null_org(self) -> bool:
        return self.normalized_org_id is None

    @property
    def is_admin(self) -> bool:
        if self.is_null_org:
            return get_default_config().is_admin_email(self.email)
        return self.role.strip().lower() in _ADMIN_ROLES


def normalize_org_id(user_id: str, org_id: str | None) -> str | None:
    """Normalize personal OpenHands orgs to the integrations-hub null org."""
    normalized_user = user_id.strip().lower()
    normalized_org = (org_id or "").strip()
    if not normalized_org or normalized_org.lower() == normalized_user:
        return None
    return normalized_org


def owner_scope_id(user_id: str, org_id: str | None) -> str:
    org_scope = org_id.strip().lower() if org_id else NULL_ORG_SCOPE
    return f"{user_id.strip().lower()}:{org_scope}"


def is_admin_owner(owner_id: str) -> bool:
    """Legacy null-org admin check for local/dev owner identifiers."""
    return get_default_config().is_admin_email(owner_id)


def reset_auth_cache() -> None:
    _AUTH_CACHE.clear()


def _cache_lookup(key: str) -> OpenHandsUser | None | object:
    entry = _AUTH_CACHE.get(key)
    if entry is None:
        return _MISS
    expires_at, user = entry
    if expires_at <= time.monotonic():
        _AUTH_CACHE.pop(key, None)
        return _MISS
    return user


def _cache_store(
    key: str, value: OpenHandsUser | None | object, ttl: float = _AUTH_CACHE_TTL_SECONDS
) -> None:
    if len(_AUTH_CACHE) >= _AUTH_CACHE_MAX_ENTRIES:
        try:
            _AUTH_CACHE.pop(next(iter(_AUTH_CACHE)), None)
        except StopIteration:
            pass
    _AUTH_CACHE[key] = (time.monotonic() + ttl, value)


def _parse_user(payload: object) -> OpenHandsUser | None:
    if not isinstance(payload, dict):
        return None
    user_id = payload.get("id")
    org_id = payload.get("org_id")
    email = payload.get("email")
    if not isinstance(user_id, str) or not user_id:
        return None
    if org_id is not None and not isinstance(org_id, str):
        return None
    if not isinstance(email, str) or not email:
        return None
    role = payload.get("role")
    permissions = payload.get("permissions")
    org_name = payload.get("org_name")
    return OpenHandsUser(
        id=user_id,
        org_id=org_id,
        email=email,
        role=role if isinstance(role, str) else "",
        permissions=tuple(
            item for item in (permissions or []) if isinstance(item, str)
        ),
        org_name=org_name if isinstance(org_name, str) else None,
    )


def _call_openhands_users_me(
    credential: str,
    requested_org_id: str | None = None,
    *,
    cookie_name: str | None = None,
) -> OpenHandsUser | None:
    """Resolve a bearer token or browser cookie to an OpenHands user.

    Returns ``None`` only when the token is definitively invalid (401/403).
    Raises :class:`AuthUnavailable` for transient/infrastructure failures
    (network errors, 5xx) so callers can surface 503 instead of 401 and
    avoid forcing a new login for a still-valid browser session.
    """
    cfg = get_default_config()
    if not cfg.openhands_base_url:
        _logger.warning(
            "OpenHands auth requested but INTHUB_OPENHANDS_BASE_URL is unset"
        )
        raise AuthUnavailable("OpenHands auth base URL is not configured")
    headers = (
        {"Cookie": f"{cookie_name}={credential}"}
        if cookie_name
        else {"Authorization": f"Bearer {credential}"}
    )
    if requested_org_id:
        headers["X-Org-Id"] = requested_org_id
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(cfg.openhands_user_me_url, headers=headers)
    except httpx.RequestError as exc:
        _logger.warning("OpenHands auth request failed: %s", exc)
        raise AuthUnavailable("OpenHands Cloud is temporarily unreachable") from exc
    if response.status_code in {401, 403}:
        return None
    if not response.is_success:
        _logger.warning("OpenHands auth returned status %s", response.status_code)
        raise AuthUnavailable(f"OpenHands Cloud returned status {response.status_code}")
    try:
        return _parse_user(response.json())
    except ValueError as exc:
        _logger.warning("OpenHands auth returned invalid JSON: %s", exc)
        raise AuthUnavailable("OpenHands Cloud returned an invalid response") from exc


def _authenticate_credential(
    credential: str,
    requested_org_id: str | None = None,
    *,
    cookie_name: str | None = None,
) -> OpenHandsUser | None:
    credential = credential.strip()
    org_key = (requested_org_id or "").strip().lower()
    if not credential:
        return None
    auth_kind = f"cookie:{cookie_name}" if cookie_name else "bearer"
    cache_key = f"{auth_kind}\0{credential}\0{org_key}"
    cached = _cache_lookup(cache_key)
    if cached is not _MISS:
        if cached is _UNAVAILABLE:
            raise AuthUnavailable("OpenHands Cloud is temporarily unreachable")
        return cached if isinstance(cached, OpenHandsUser) else None
    try:
        user = _call_openhands_users_me(
            credential,
            requested_org_id,
            cookie_name=cookie_name,
        )
    except AuthUnavailable:
        _cache_store(cache_key, _UNAVAILABLE, ttl=_AUTH_UNAVAILABLE_CACHE_TTL_SECONDS)
        raise
    _cache_store(cache_key, user)
    return user


def authenticate_token(
    token: str, requested_org_id: str | None = None
) -> OpenHandsUser | None:
    return _authenticate_credential(token, requested_org_id)


def authenticate_cookie(
    cookie: str,
    requested_org_id: str | None = None,
) -> OpenHandsUser | None:
    return _authenticate_credential(
        cookie,
        requested_org_id,
        cookie_name=get_default_config().openhands_auth_cookie_name,
    )
