"""Hosted pre-save LLM connection check for ``POST /api/v1/settings/profiles/{name}/validate``.

The agent server answers ``POST /api/profiles/{name}/validate`` for the local
Agent Canvas; this is the app-server counterpart with the same response
contract (the SDK's ``ValidateProfileResponse``) and the same single bounded
ping (``_preflight_llm``: one attempt, no fallbacks, at most 15 s, 1 token).

Threat model
------------
The ping makes the app-server pod call a user-chosen ``base_url`` with a
user-held credential, so the route:

- resolves credentials server-side only (stored profile key, the user's
  settings key, or the org provider connection the draft links to) and never
  returns them; the key value is scrubbed from any upstream error text;
- applies the MCP connection test's SSRF rule to the resolved ``base_url``:
  loopback, link-local (instance metadata), unspecified, multicast and reserved
  addresses are refused, RFC 1918 / ULA ranges stay reachable for self-hosted
  LLM gateways. Set ``LLM_VALIDATION_ALLOW_LOCAL_TARGETS=true`` for local
  development against an LLM on localhost;
- allows ``RATE_LIMIT_REQUESTS`` checks per user per ``RATE_LIMIT_SECONDS``,
  per process, on top of the app-wide per-client limit.

Residual risk: the address check and the provider call resolve DNS
independently (DNS rebinding), and a draft with no ``base_url`` is sent where
LiteLLM routes the model by default.
"""

import asyncio
import ipaddress
import os
import socket
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status
from pydantic import SecretStr

from openhands.agent_server.profiles_router import (
    _TRANSIENT_ERROR_TYPES,
    ValidateProfileError,
    ValidateProfileResponse,
    _preflight_llm,
)
from openhands.app_server.settings.provider_connections import ProviderConnections
from openhands.app_server.user_auth.user_auth import get_user_auth
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import is_quota_exhaustion_error
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE
from openhands.sdk.utils.redact import redact_text_secrets

RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_SECONDS = 60
_ALLOW_LOCAL_TARGETS = os.environ.get(
    'LLM_VALIDATION_ALLOW_LOCAL_TARGETS', 'false'
).lower() in ('true', '1')

_PING_MESSAGES = [
    Message(role='system', content=[TextContent(text='Reply with one token.')]),
    Message(role='user', content=[TextContent(text='ping')]),
]

_recent_checks: defaultdict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(user_id: str | None) -> None:
    """Raise 429 once the user exceeds ``RATE_LIMIT_REQUESTS`` per window."""
    now = time.monotonic()
    history = _recent_checks[user_id or '<anonymous>']
    while history and history[0] <= now - RATE_LIMIT_SECONDS:
        history.popleft()
    if len(history) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail='Too many LLM connection checks. Try again in a minute.',
        )
    history.append(now)


async def get_provider_connections(request: Request) -> ProviderConnections:
    """The caller's org provider connections; empty when the auth has none."""
    user_auth = await get_user_auth(request)
    getter = getattr(user_auth, 'get_provider_connections', None)
    if getter is None:
        return ProviderConnections()
    return await getter()


def _resolve_addresses(
    host: str, port: int | None
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        address: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(
            info[4][0]
        )
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        addresses.append(address)
    return addresses


def _target_rejection(base_url: str) -> str | None:
    """Why ``base_url`` must not be called from the app server, or ``None``. Blocking."""
    invalid = 'Base URL must be an http:// or https:// URL with a host.'
    try:
        parts = urlsplit(base_url)
        host, port = parts.hostname, parts.port
    except ValueError:
        return invalid
    if parts.scheme.lower() not in ('http', 'https') or not host:
        return invalid
    if _ALLOW_LOCAL_TARGETS:
        return None
    try:
        addresses = _resolve_addresses(host, port)
    except (OSError, UnicodeError, ValueError):
        # The ping reports an unresolvable host as a connection error.
        return None
    for address in addresses:
        if (
            address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
            or address.is_reserved
        ):
            return (
                f'Base URL host {host!r} resolves to {address}, which cannot be '
                'checked from the app server.'
            )
    return None


async def check_target(llm: LLM) -> None:
    """Raise 422 for drafts the app server must not or cannot ping."""
    if llm.auth_type == 'subscription':
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Subscription sign-in profiles cannot be checked from the app server.',
        )
    if not llm.base_url:
        return
    rejection = await asyncio.get_running_loop().run_in_executor(
        None, _target_rejection, llm.base_url
    )
    if rejection is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=rejection
        )


def _scrub(text: str, llm: LLM) -> str:
    text = redact_text_secrets(text)
    if isinstance(llm.api_key, SecretStr):
        secret = llm.api_key.get_secret_value()
        if secret:
            text = text.replace(secret, REDACTED_SECRET_VALUE)
    return text


async def ping_llm(name: str, llm: LLM) -> ValidateProfileResponse:
    """Send the SDK's bounded 1-token pre-flight and classify the outcome.

    Rate limits and timeouts are transient and do not fail the check, except a
    429 that reports an exhausted quota; every other error does.
    """
    llm = _preflight_llm(llm)
    try:
        if llm.uses_responses_api():
            await llm.aresponses(messages=_PING_MESSAGES, max_tokens=1)
        else:
            await llm.acompletion(messages=_PING_MESSAGES, max_tokens=1)
    except _TRANSIENT_ERROR_TYPES as exc:
        if not is_quota_exhaustion_error(exc):
            logger.info(
                'settings:profile_validate_transient',
                extra={'profile': name, 'error_type': type(exc).__name__},
            )
            return ValidateProfileResponse(valid=True)
        return _invalid(name, exc, llm)
    except Exception as exc:
        return _invalid(name, exc, llm)
    return ValidateProfileResponse(valid=True)


def _invalid(name: str, exc: Exception, llm: LLM) -> ValidateProfileResponse:
    error_type = type(exc).__name__
    message = _scrub(getattr(exc, 'message', None) or str(exc) or error_type, llm)
    logger.info(
        'settings:profile_validate_failed',
        extra={'profile': name, 'error_type': error_type},
    )
    return ValidateProfileResponse(
        valid=False, error=ValidateProfileError(type=error_type, message=message)
    )
