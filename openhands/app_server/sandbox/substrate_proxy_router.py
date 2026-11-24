"""The app's proxy to ports on Substrate sandboxes.

atenet-router only admits clients holding an allowlisted certificate, and
picks the actor from a header, so a browser cannot reach an actor itself. The
app holds the certificate and serves each port of a sandbox at
``/api/v1/sandbox-proxy/{sandbox_id}/{port}/...``, for both HTTP and
WebSockets. The prefix is stripped before the request reaches the actor.

A caller must hold the sandbox's session API key, or be signed in as the
user who owns the sandbox. The app's own credentials are removed before a
request is forwarded, and the cookies a sandbox sets are kept to its prefix,
so a sandbox never sees the user's session and cannot replace it.
"""

import logging
import re
from typing import cast

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from starlette.background import BackgroundTask
from starlette.requests import HTTPConnection
from websockets.frames import CloseCode

from openhands.app_server.sandbox.sandbox_store import (
    SUBSTRATE_BACKEND,
    LifecycleState,
    StoredSandbox,
    hash_session_api_key,
)
from openhands.app_server.sandbox.substrate.ingress import (
    RouterTransport,
    actor_url,
    bridge_websocket,
)
from openhands.app_server.sandbox.substrate_sandbox_service import (
    PROXY_PATH,
    SubstrateSandboxServiceInjector,
)
from openhands.app_server.services.db_session import get_db_session
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user_auth.user_auth import get_user_auth

_logger = logging.getLogger(__name__)

router = APIRouter(prefix='/sandbox-proxy', include_in_schema=False)

SESSION_API_KEY_HEADER = 'x-session-api-key'
# The agent server takes the session key as a query parameter on WebSockets.
SESSION_API_KEY_PARAM = 'session_api_key'
METHODS = ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']

HOP_BY_HOP_HEADERS = frozenset(
    {
        'connection',
        'keep-alive',
        'proxy-authenticate',
        'proxy-authorization',
        'te',
        'trailer',
        'transfer-encoding',
        'upgrade',
    }
)
# The app server's own server adds these to every response.
SERVER_RESPONSE_HEADERS = frozenset({'date', 'server'})
# The app's own credentials. A sandbox runs code the user did not write, so
# it must never receive them.
APP_CREDENTIAL_HEADERS = frozenset({'authorization', 'x-access-token'})
APP_COOKIE_PREFIXES = ('openhands_auth', 'keycloak_auth', 'api_key')
# Headers the proxy sets itself, or that only describe the browser's
# connection to the app.
REPLACED_HEADERS = frozenset(
    {
        'host',
        'cookie',
        'x-forwarded-for',
        'x-forwarded-host',
        'x-forwarded-proto',
        'x-forwarded-prefix',
    }
)
WEBSOCKET_HANDSHAKE_HEADERS = frozenset(
    {
        'sec-websocket-key',
        'sec-websocket-version',
        'sec-websocket-extensions',
        'sec-websocket-protocol',
        'sec-websocket-accept',
    }
)
# A sandbox's pages get this policy unless they send their own, in place of
# the app's: the app's would block the VS Code workbench.
SANDBOX_CONTENT_SECURITY_POLICY = "frame-ancestors 'self'"

_ROUTER_CLIENT_ATTR = 'substrate_router_client'


def _injector() -> SubstrateSandboxServiceInjector:
    from openhands.app_server.config import get_global_config

    injector = get_global_config().sandbox
    if not isinstance(injector, SubstrateSandboxServiceInjector):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return injector


def _router_client(connection: HTTPConnection) -> httpx.AsyncClient:
    """The app's pooled client for actor ports.

    One per app, so tunnels are reused across requests.
    """
    client = getattr(connection.app.state, _ROUTER_CLIENT_ATTR, None)
    if client is None:
        client = httpx.AsyncClient(
            transport=RouterTransport(_injector().router_settings()),
            # A conversation's requests can run for as long as the agent works.
            timeout=httpx.Timeout(30.0, read=None),
        )
        setattr(connection.app.state, _ROUTER_CLIENT_ATTR, client)
    return client


def _prefix(sandbox_id: str, port: int) -> str:
    return f'{PROXY_PATH}/{sandbox_id}/{port}'


def _is_app_cookie(name: str) -> bool:
    return name.startswith(APP_COOKIE_PREFIXES)


def _sandbox_cookies(cookie_header: str) -> str | None:
    """The Cookie header without the app's own cookies."""
    kept = []
    for pair in cookie_header.split(';'):
        name = pair.split('=', 1)[0].strip()
        if name and not _is_app_cookie(name):
            kept.append(pair.strip())
    return '; '.join(kept) or None


def forward_headers(
    connection: HTTPConnection, prefix: str, skip: frozenset[str] = frozenset()
) -> list[tuple[str, str]]:
    """The browser's headers as the actor should see them."""
    connection_tokens = {
        token.strip().lower()
        for token in connection.headers.get('connection', '').split(',')
    }
    headers = []
    for name, value in connection.headers.items():
        lower = name.lower()
        if (
            lower in HOP_BY_HOP_HEADERS
            or lower in connection_tokens
            or lower in APP_CREDENTIAL_HEADERS
            or lower in REPLACED_HEADERS
            or lower in skip
        ):
            continue
        headers.append((name, value))
    cookies = _sandbox_cookies(connection.headers.get('cookie', ''))
    if cookies:
        headers.append(('cookie', cookies))
    host = connection.headers.get('host') or connection.url.netloc
    scheme = 'https' if connection.url.scheme in ('https', 'wss') else 'http'
    headers.extend(
        [
            ('x-forwarded-host', connection.headers.get('x-forwarded-host', host)),
            (
                'x-forwarded-proto',
                connection.headers.get('x-forwarded-proto', scheme),
            ),
            ('x-forwarded-prefix', prefix),
        ]
    )
    if connection.client:
        headers.append(('x-forwarded-for', connection.client.host))
    return headers


def _rewrite_location(location: str, prefix: str, actor_origin: str) -> str:
    """Keep a redirect inside the sandbox's prefix."""
    if location.startswith(actor_origin):
        location = location[len(actor_origin) :] or '/'
    if location.startswith('/') and not location.startswith('//'):
        return f'{prefix}{location}'
    return location


_PATH_ATTRIBUTE = re.compile(r';\s*path=([^;]*)', re.IGNORECASE)
_DOMAIN_ATTRIBUTE = re.compile(r';\s*domain=[^;]*', re.IGNORECASE)


def _rewrite_set_cookie(value: str, prefix: str) -> str | None:
    """Scope a cookie a sandbox sets to the sandbox's prefix.

    A cookie with an app cookie's name is dropped: the browser would send it
    to the app's own routes too, where it would shadow the user's session.
    """
    name = value.split('=', 1)[0].strip()
    if _is_app_cookie(name):
        return None
    value = _DOMAIN_ATTRIBUTE.sub('', value)

    def scoped(match: re.Match[str]) -> str:
        path = match.group(1).strip() or '/'
        return f'; Path={prefix}{path if path.startswith("/") else "/" + path}'

    if _PATH_ATTRIBUTE.search(value):
        return _PATH_ATTRIBUTE.sub(scoped, value, count=1)
    return f'{value}; Path={prefix}/'


def response_headers(
    response: httpx.Response, prefix: str, actor_origin: str
) -> list[tuple[str, str]]:
    """The actor's response headers as the browser should see them."""
    headers = []
    has_policy = False
    for raw_name, raw_value in response.headers.raw:
        name = raw_name.decode('latin-1')
        value = raw_value.decode('latin-1')
        lower = name.lower()
        if lower in HOP_BY_HOP_HEADERS or lower in SERVER_RESPONSE_HEADERS:
            continue
        if lower == 'location':
            value = _rewrite_location(value, prefix, actor_origin)
        elif lower == 'set-cookie':
            rewritten = _rewrite_set_cookie(value, prefix)
            if rewritten is None:
                continue
            value = rewritten
        elif lower == 'content-security-policy':
            has_policy = True
        headers.append((name, value))
    if not has_policy:
        headers.append(('content-security-policy', SANDBOX_CONTENT_SECURITY_POLICY))
    return headers


async def _caller_user_id(connection: HTTPConnection) -> str | None:
    try:
        user_auth = await get_user_auth(cast(Request, connection))
        return await user_auth.get_user_id()
    except Exception:
        return None


async def authorize(connection: HTTPConnection, sandbox_id: str) -> StoredSandbox:
    """The sandbox a caller may reach through the proxy.

    The caller holds the sandbox's session key, or is signed in as its owner.
    A paused sandbox is refused, because atenet-router would resume it.
    """
    state = InjectorState()
    async with get_db_session(state) as db_session:
        stored_sandbox = (
            await db_session.execute(
                select(StoredSandbox).where(
                    StoredSandbox.backend == SUBSTRATE_BACKEND,
                    StoredSandbox.id == sandbox_id,
                )
            )
        ).scalar_one_or_none()
    if stored_sandbox is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Sandbox not found')

    session_api_key = connection.headers.get(
        SESSION_API_KEY_HEADER
    ) or connection.query_params.get(SESSION_API_KEY_PARAM)
    if session_api_key:
        if hash_session_api_key(session_api_key) != stored_sandbox.session_api_key_hash:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'Invalid session API key')
    else:
        user_id = await _caller_user_id(connection)
        if user_id is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'Not signed in')
        if user_id != stored_sandbox.created_by_user_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, 'Sandbox not found')

    if stored_sandbox.lifecycle_state == LifecycleState.PAUSED:
        raise HTTPException(status.HTTP_409_CONFLICT, 'Sandbox is paused')
    return stored_sandbox


def _target(path: str, query: str) -> str:
    return f'/{path}' + (f'?{query}' if query else '')


@router.api_route('/{sandbox_id}/{port}', methods=METHODS)
@router.api_route('/{sandbox_id}/{port}/{path:path}', methods=METHODS)
async def proxy_http(
    request: Request, sandbox_id: str, port: int, path: str = ''
) -> Response:
    _injector()
    if not 0 < port < 65536:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await authorize(request, sandbox_id)

    prefix = _prefix(sandbox_id, port)
    has_body = 'content-length' in request.headers or (
        'transfer-encoding' in request.headers
    )
    client = _router_client(request)
    upstream = client.build_request(
        request.method,
        actor_url(sandbox_id, port, _target(path, request.url.query)),
        headers=forward_headers(request, prefix),
        content=request.stream() if has_body else None,
    )
    try:
        response = await client.send(upstream, stream=True)
    except httpx.ConnectError as exc:
        _logger.warning(
            'substrate.proxy_connect_failed',
            extra={'sandbox_id': sandbox_id, 'port': port, 'error': str(exc)},
        )
        return Response('The sandbox is not reachable', status.HTTP_502_BAD_GATEWAY)
    except httpx.HTTPError as exc:
        _logger.info(
            'substrate.proxy_request_failed',
            extra={'sandbox_id': sandbox_id, 'port': port, 'error': str(exc)},
        )
        return Response('The sandbox did not answer', status.HTTP_502_BAD_GATEWAY)

    return _streaming_response(response, prefix, sandbox_id, port)


def _streaming_response(
    response: httpx.Response, prefix: str, sandbox_id: str, port: int
) -> StreamingResponse:
    proxied = StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        background=BackgroundTask(response.aclose),
    )
    # Raw headers keep repeated ones, such as several Set-Cookie.
    proxied.raw_headers = [
        (name.lower().encode('latin-1'), value.encode('latin-1'))
        for name, value in response_headers(
            response, prefix, actor_url(sandbox_id, port, '').rstrip('/')
        )
    ]
    return proxied


@router.websocket('/{sandbox_id}/{port}')
@router.websocket('/{sandbox_id}/{port}/{path:path}')
async def proxy_websocket(
    websocket: WebSocket, sandbox_id: str, port: int, path: str = ''
) -> None:
    try:
        injector = _injector()
        if not 0 < port < 65536:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        await authorize(websocket, sandbox_id)
    except HTTPException:
        await websocket.close(code=CloseCode.POLICY_VIOLATION)
        return

    prefix = _prefix(sandbox_id, port)
    await bridge_websocket(
        websocket,
        injector.router_settings(),
        sandbox_id,
        port,
        _target(path, websocket.url.query),
        forward_headers(websocket, prefix, skip=WEBSOCKET_HANDSHAKE_HEADERS),
    )
