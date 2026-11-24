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
- sends a stored key (profile or settings) only to the provider and base URL
  it was stored with; a draft that points elsewhere must carry a typed key;
- only pings with an explicit API key. LiteLLM falls back to the process
  environment (``OPENAI_API_KEY``, ``AWS_*``, cloud workload identity) when a
  call has no credential, and the app server's own credentials must never reach
  a user-chosen endpoint. Keyless drafts and providers that sign in with cloud
  identity (Bedrock, SageMaker, Vertex AI) are therefore not checked;
- applies the MCP connection test's SSRF rule to every endpoint URL the call
  can use (``base_url``, ``aws_bedrock_runtime_endpoint``): loopback,
  link-local (instance metadata), unspecified, multicast and reserved
  addresses are refused, RFC 1918 / ULA ranges stay reachable for self-hosted
  LLM gateways. Set ``LLM_VALIDATION_ALLOW_LOCAL_TARGETS=true`` for local
  development against an LLM on localhost;
- allows ``RATE_LIMIT_REQUESTS`` checks per user per ``RATE_LIMIT_SECONDS``,
  per process, on top of the app-wide per-client limit.

A timeout or a provider rate limit is "no verdict", not ``valid=true``: the
route answers 422 so a caller never shows an unreached endpoint as verified.

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
from openhands.app_server.settings.llm_profiles import has_real_api_key
from openhands.app_server.settings.provider_connections import ProviderConnections
from openhands.app_server.user_auth.user_auth import get_user_auth
from openhands.app_server.utils.llm import resolve_llm_base_url
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import is_quota_exhaustion_error
from openhands.sdk.llm.utils.litellm_provider import LLMProvider
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE
from openhands.sdk.utils.redact import redact_text_secrets

RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_SECONDS = 60
MAX_ERROR_MESSAGE_CHARS = 500
_CLOUD_IDENTITY_PROVIDERS = frozenset(
    {
        'bedrock',
        'bedrock_mantle',
        'sagemaker',
        'sagemaker_chat',
        'sagemaker_nova',
        'vertex_ai',
        'vertex_ai_beta',
    }
)
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


def _target_rejection(url: str, label: str = 'Base URL') -> str | None:
    """Why ``url`` must not be called from the app server, or ``None``. Blocking."""
    invalid = f'{label} must be an http:// or https:// URL with a host.'
    try:
        parts = urlsplit(url)
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
                f'{label} host {host!r} resolves to {address}, which cannot be '
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
    endpoints = [
        (url, label)
        for url, label in (
            (llm.base_url, 'Base URL'),
            (llm.aws_bedrock_runtime_endpoint, 'Bedrock runtime endpoint'),
        )
        if url
    ]
    loop = asyncio.get_running_loop()
    for url, label in endpoints:
        rejection = await loop.run_in_executor(None, _target_rejection, url, label)
        if rejection is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=rejection
            )
    provider = LLMProvider.from_model(model=llm.model, api_base=llm.base_url).name
    if provider in _CLOUD_IDENTITY_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                'Bedrock, SageMaker and Vertex AI profiles are not checked from '
                'the app server.'
            ),
        )
    if not has_real_api_key(llm.api_key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Profiles without an API key are not checked before saving.',
        )


def _endpoint(llm: LLM, managed_proxy_url: str) -> tuple[str | None, str | None]:
    base_url = resolve_llm_base_url(
        model=llm.model, base_url=llm.base_url, managed_proxy_url=managed_proxy_url
    )
    provider = LLMProvider.from_model(model=llm.model, api_base=base_url).name
    return provider, base_url.rstrip('/') if base_url else None


def _key_text(api_key: str | SecretStr | None) -> str | None:
    return api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key


def stored_key_owner(llm: LLM, settings_llm: LLM, profile: LLM | None) -> LLM | None:
    """Return the stored config whose key ``llm`` would send, or ``None`` for a typed key.

    Judged on the merged key, not the request body: a masked placeholder
    merges back to the stored settings key.
    """
    if not has_real_api_key(llm.api_key):
        return settings_llm
    key = _key_text(llm.api_key)
    for owner in (profile, settings_llm):
        if (
            owner is not None
            and has_real_api_key(owner.api_key)
            and _key_text(owner.api_key) == key
        ):
            return owner
    return None


def check_stored_key_endpoint(llm: LLM, key_owner: LLM, managed_proxy_url: str) -> None:
    """Raise 422 when a stored key would go to an endpoint it was not stored for."""
    if _endpoint(llm, managed_proxy_url) != _endpoint(key_owner, managed_proxy_url):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                'Enter the API key to check a profile that uses a different '
                'provider or base URL than the stored key.'
            ),
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

    Rate limits and timeouts give no verdict (422), except a 429 that reports
    an exhausted quota; every other error makes the draft invalid.
    """
    llm = _preflight_llm(llm)
    try:
        if llm.uses_responses_api():
            await llm.aresponses(messages=_PING_MESSAGES, max_tokens=1)
        else:
            await llm.acompletion(messages=_PING_MESSAGES, max_tokens=1)
    except _TRANSIENT_ERROR_TYPES as exc:
        if not is_quota_exhaustion_error(exc):
            error_type = type(exc).__name__
            logger.info(
                'settings:profile_validate_transient',
                extra={'profile': name, 'error_type': error_type},
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f'The provider did not answer the check ({error_type}), '
                    'so the connection was not verified.'
                ),
            ) from exc
        return _invalid(name, exc, llm)
    except Exception as exc:
        return _invalid(name, exc, llm)
    return ValidateProfileResponse(valid=True)


def _error_message(exc: Exception, llm: LLM) -> str:
    raw = (getattr(exc, 'message', None) or str(exc)).strip()
    # Later lines can hold a server-side traceback (LiteLLM's APIConnectionError).
    message = _scrub(raw.splitlines()[0] if raw else type(exc).__name__, llm)
    if len(message) > MAX_ERROR_MESSAGE_CHARS:
        message = message[: MAX_ERROR_MESSAGE_CHARS - 3] + '...'
    return message


def _invalid(name: str, exc: Exception, llm: LLM) -> ValidateProfileResponse:
    error_type = type(exc).__name__
    message = _error_message(exc, llm)
    logger.info(
        'settings:profile_validate_failed',
        extra={'profile': name, 'error_type': error_type},
    )
    return ValidateProfileResponse(
        valid=False, error=ValidateProfileError(type=error_type, message=message)
    )
