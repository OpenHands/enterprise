from collections.abc import Awaitable
from typing import Callable

from fastapi import Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


class SetAuthCookieMiddleware:
    async def __call__(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        from server.auth.composition import get_auth_services

        return await get_auth_services().browser(request, call_next)


_CREDENTIALLESS_PATH_PREFIXES = (
    # RFC 8628 device authorization endpoints — unauthenticated by design,
    # called cross-origin from clients that are exchanging device codes for
    # API keys.
    '/oauth/device/authorize',
    '/oauth/device/token',
)


class ApiKeyAwareCORSMiddleware:
    """CORS dispatcher that loosens the policy for credential-less requests.

    Requests that authenticate via API key (``Authorization: Bearer …``,
    ``X-Session-API-Key``, or ``X-Access-Token``) or that target a known
    unauthenticated cross-origin endpoint (RFC 8628 device flow) get
    ``Access-Control-Allow-Origin: *`` with credentials disabled — the
    wildcard is safe because the browser cannot attach cookies when
    credentials are off, so the only way to authenticate is the explicit
    key (or no auth, for public endpoints).

    Cookie/session requests keep the strict origin allowlist with
    credentials enabled.
    """

    def __init__(self, app: ASGIApp, allow_origins: list[str]) -> None:
        from server.auth.composition import get_auth_services

        policy = get_auth_services().browser
        self._permissive = CORSMiddleware(
            policy.cors_app(app, permissive=True),
            allow_origins=['*'],
            allow_credentials=False,
            allow_methods=['*'],
            allow_headers=['*'],
        )
        self._strict = CORSMiddleware(
            policy.cors_app(app, permissive=False),
            allow_origins=allow_origins,
            allow_credentials=True,
            allow_methods=['*'],
            allow_headers=['*'],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'http' and self._is_credentialless(scope):
            await self._permissive(scope, receive, send)
        else:
            await self._strict(scope, receive, send)

    @staticmethod
    def _is_credentialless(scope: Scope) -> bool:
        path = scope.get('path', '')
        if any(path.startswith(prefix) for prefix in _CREDENTIALLESS_PATH_PREFIXES):
            return True
        if scope['method'] == 'OPTIONS':
            # Preflight: the auth header hasn't been sent yet, so look at the
            # headers the browser is asking permission to send. Parse the
            # comma-separated list into a set so we match whole header names
            # only — otherwise something like ``x-my-authorization-token``
            # would substring-match ``authorization``.
            for name, value in scope['headers']:
                if name == b'access-control-request-headers':
                    requested_headers = {
                        h.strip() for h in value.decode('latin-1').lower().split(',')
                    }
                    return bool(
                        requested_headers
                        & {'authorization', 'x-session-api-key', 'x-access-token'}
                    )
            return False
        for name, value in scope['headers']:
            if name == b'authorization' and value[:7].lower() == b'bearer ':
                return True
            if name in (b'x-session-api-key', b'x-access-token'):
                return True
        return False


class PostHogSessionMiddleware:
    """Extract the PostHog session ID from the incoming request header.

    Stores the value on ``request.state.posthog_session_id`` so that
    subsequent event-capture call sites can link server-side events to the
    corresponding frontend session-replay recording.

    When the ``X-POSTHOG-SESSION-ID`` header is absent the attribute is set
    to ``None`` — never raises, never blocks.
    """

    async def __call__(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.posthog_session_id = request.headers.get('X-POSTHOG-SESSION-ID')
        return await call_next(request)
