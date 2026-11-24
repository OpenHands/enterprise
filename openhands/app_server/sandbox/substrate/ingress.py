"""Reach a port on a Substrate actor through atenet-router.

atenet-router admits a client only with a certificate whose SPIFFE ID is on
its allowlist, and picks the actor from the ``ate-target-actor`` header. A
port other than the actor's default 80 is reached with an HTTP ``CONNECT`` to
the router's CONNECT listener, naming the port in the authority. Inside the
tunnel the actor speaks plain HTTP on that port.

Every request here goes through such a tunnel: ``RouterTransport`` runs httpx
requests over them, and ``bridge_websocket`` relays a browser WebSocket.
"""

import logging
import select
import ssl
from dataclasses import dataclass

import anyio
import anyio.abc
import httpcore
import httpx
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.client import ClientProtocol
from websockets.frames import CloseCode, Frame, Opcode
from websockets.http11 import Response
from websockets.protocol import State
from websockets.typing import Subprotocol
from websockets.uri import parse_uri

_logger = logging.getLogger(__name__)

TARGET_ACTOR_HEADER = 'ate-target-actor'
MAX_RESPONSE_HEAD_BYTES = 64 * 1024
# Agent events can carry whole files, so this is well above websockets' 1 MiB
# default. It bounds what one actor message can make the proxy buffer.
MAX_WEBSOCKET_MESSAGE_BYTES = 64 * 1024 * 1024


class RouterError(Exception):
    """atenet-router refused a tunnel, or could not be reached.

    ``status`` is the router's HTTP status, or None when there was no answer.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RouterSettings:
    """How the app reaches atenet-router."""

    # host:port of the router's CONNECT listener.
    address: str
    atespace: str
    # The client certificate chain, PEM. With no key_file, the key is in the
    # same file, as in a podCertificate credential bundle.
    cert_file: str
    key_file: str | None = None
    # CA bundle the router's serving certificate is checked against.
    ca_file: str | None = None
    # Name the router's certificate is checked against, when address is not
    # that name, such as a port-forward.
    server_name: str | None = None
    connect_timeout: float = 30.0

    def ssl_context(self) -> ssl.SSLContext:
        """A TLS context with the current files.

        It is built for every tunnel because the kubelet rotates the pod
        certificate in place.
        """
        context = ssl.create_default_context(cafile=self.ca_file)
        context.load_cert_chain(self.cert_file, self.key_file)
        return context


def actor_url(actor: str, port: int, target: str = '/') -> str:
    """The URL ``RouterTransport`` sends to a port on an actor."""
    return f'http://{actor}:{port}{target}'


async def open_tunnel(
    settings: RouterSettings, actor: str, port: int
) -> tuple[anyio.abc.ByteStream, bytes]:
    """Open a tunnel to a port on an actor.

    Returns the stream and any bytes that arrived after the router's answer.
    The router resumes a suspended actor before it answers.
    """
    host, _, router_port = settings.address.rpartition(':')
    authority = f'{actor}:{port}'
    request = (
        f'CONNECT {authority} HTTP/1.1\r\n'
        f'Host: {authority}\r\n'
        f'{TARGET_ACTOR_HEADER}: {settings.atespace}/{actor}\r\n'
        '\r\n'
    ).encode()
    try:
        with anyio.fail_after(settings.connect_timeout):
            stream = await anyio.connect_tcp(
                host,
                int(router_port),
                ssl_context=settings.ssl_context(),
                tls_hostname=settings.server_name or host,
                # Envoy does not always send close_notify.
                tls_standard_compatible=False,
            )
            try:
                await stream.send(request)
                head = b''
                while b'\r\n\r\n' not in head:
                    if len(head) > MAX_RESPONSE_HEAD_BYTES:
                        raise RouterError('atenet-router sent an oversized answer')
                    head += await stream.receive()
            except BaseException:
                await stream.aclose()
                raise
    except TimeoutError as exc:
        raise RouterError(
            f'atenet-router did not open a tunnel to {authority} within '
            f'{settings.connect_timeout}s'
        ) from exc
    except anyio.EndOfStream as exc:
        raise RouterError(
            "atenet-router closed the connection. Check that the app's SPIFFE ID "
            'is in its --ingress-allowed-spiffe-ids.'
        ) from exc
    except (OSError, ssl.SSLError, anyio.BrokenResourceError) as exc:
        raise RouterError(f'Could not reach atenet-router: {exc}') from exc

    head, _, rest = head.partition(b'\r\n\r\n')
    status_line = head.split(b'\r\n', 1)[0].decode('latin-1')
    parts = status_line.split(' ', 2)
    status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    if status is None or not 200 <= status < 300:
        await stream.aclose()
        raise RouterError(
            f'atenet-router refused a tunnel to {authority}: {status_line}', status
        )
    return stream, rest


class _TunnelStream(httpcore.AsyncNetworkStream):
    """An httpcore stream over a router tunnel."""

    def __init__(self, stream: anyio.abc.ByteStream, buffered: bytes):
        self._stream = stream
        self._buffered = buffered

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if self._buffered:
            data, self._buffered = (
                self._buffered[:max_bytes],
                self._buffered[max_bytes:],
            )
            return data
        try:
            with anyio.fail_after(timeout):
                return await self._stream.receive(max_bytes)
        except TimeoutError as exc:
            raise httpcore.ReadTimeout(exc) from exc
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            return b''
        except (anyio.BrokenResourceError, OSError, ssl.SSLError) as exc:
            raise httpcore.ReadError(exc) from exc

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        if not buffer:
            return
        try:
            with anyio.fail_after(timeout):
                await self._stream.send(buffer)
        except TimeoutError as exc:
            raise httpcore.WriteTimeout(exc) from exc
        except (
            anyio.BrokenResourceError,
            anyio.ClosedResourceError,
            OSError,
            ssl.SSLError,
        ) as exc:
            raise httpcore.WriteError(exc) from exc

    async def aclose(self) -> None:
        try:
            await self._stream.aclose()
        except (OSError, ssl.SSLError, anyio.BrokenResourceError):
            pass

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError('Actor ports are plain HTTP inside the tunnel')

    def get_extra_info(self, info: str):
        if info != 'is_readable':
            return None
        # httpcore drops an idle connection the router has closed: a readable
        # socket with no request in flight can only be at its end.
        sock = self._stream.extra(anyio.abc.SocketAttribute.raw_socket, None)
        if sock is None or sock.fileno() < 0:
            return True
        readable, _, _ = select.select([sock], [], [], 0)
        return bool(readable)


class _TunnelBackend(httpcore.AsyncNetworkBackend):
    """Connects to ``{actor}:{port}`` by opening a router tunnel."""

    def __init__(self, settings: RouterSettings):
        self.settings = settings

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            stream, buffered = await open_tunnel(self.settings, host, port)
        except RouterError as exc:
            raise httpcore.ConnectError(str(exc)) from exc
        return _TunnelStream(stream, buffered)

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options=None
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError('Actors are reached through atenet-router')

    async def sleep(self, seconds: float) -> None:
        await anyio.sleep(seconds)


class RouterTransport(httpx.AsyncHTTPTransport):
    """An httpx transport that sends each request through a router tunnel.

    Send requests to ``actor_url(actor, port, path)``. Connections are pooled
    per actor and port.
    """

    def __init__(
        self,
        settings: RouterSettings,
        max_connections: int = 1000,
        max_keepalive_connections: int = 200,
        keepalive_expiry: float = 15.0,
    ):
        super().__init__()
        # httpx takes no network backend. Its transport maps requests,
        # responses and errors around ``_pool``, so only the pool changes.
        self._pool = httpcore.AsyncConnectionPool(
            network_backend=_TunnelBackend(settings),
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
            keepalive_expiry=keepalive_expiry,
        )


class _WebSocketRelay:
    """Relays one browser WebSocket to an actor's, over a router tunnel."""

    def __init__(
        self,
        websocket: WebSocket,
        stream: anyio.abc.ByteStream,
        protocol: ClientProtocol,
    ):
        self.websocket = websocket
        self.stream = stream
        self.protocol = protocol
        self._send_lock = anyio.Lock()
        self._pending: list = []

    async def flush(self) -> None:
        """Send what the protocol has queued: frames, pongs, a close."""
        async with self._send_lock:
            for data in self.protocol.data_to_send():
                if data:
                    await self.stream.send(data)

    async def handshake(self, buffered: bytes) -> Response | None:
        """Complete the actor's opening handshake. None when it refused."""
        await self.flush()
        data = buffered
        while True:
            if data:
                self.protocol.receive_data(data)
            events = self.protocol.events_received()
            for index, event in enumerate(events):
                if isinstance(event, Response):
                    if self.protocol.handshake_exc is not None:
                        _logger.info(
                            'substrate.websocket_rejected',
                            extra={'error': str(self.protocol.handshake_exc)},
                        )
                        return None
                    # Frames that arrived with the response.
                    self._pending = events[index + 1 :]
                    return event
            try:
                data = await self.stream.receive()
            except anyio.EndOfStream:
                return None

    async def browser_to_actor(self) -> None:
        while True:
            message = await self.websocket.receive()
            if message['type'] == 'websocket.disconnect':
                self.protocol.send_close(
                    message.get('code') or CloseCode.NORMAL_CLOSURE
                )
                await self.flush()
                return
            if message.get('text') is not None:
                self.protocol.send_text(message['text'].encode())
            else:
                self.protocol.send_binary(message.get('bytes') or b'')
            await self.flush()

    async def _receive_events(self) -> list:
        try:
            data = await self.stream.receive()
        except (anyio.EndOfStream, anyio.BrokenResourceError):
            self.protocol.receive_eof()
        else:
            self.protocol.receive_data(data)
        return self.protocol.events_received()

    async def actor_to_browser(self) -> None:
        opcode: Opcode | None = None
        parts: list[bytes] = []
        events, self._pending = self._pending, []
        while True:
            for event in events:
                if not isinstance(event, Frame):
                    continue
                if event.opcode in (Opcode.TEXT, Opcode.BINARY):
                    opcode, parts = event.opcode, [event.data]
                elif event.opcode == Opcode.CONT:
                    parts.append(event.data)
                else:
                    continue
                if event.fin:
                    payload = b''.join(parts)
                    if opcode == Opcode.TEXT:
                        await self.websocket.send_text(payload.decode())
                    else:
                        await self.websocket.send_bytes(payload)
            # Pongs and the reply to a close.
            await self.flush()
            if self.protocol.state in (State.CLOSING, State.CLOSED):
                await self.websocket.close(
                    code=self.protocol.close_code or CloseCode.INTERNAL_ERROR,
                    reason=self.protocol.close_reason or '',
                )
                return
            events = await self._receive_events()


async def bridge_websocket(
    websocket: WebSocket,
    settings: RouterSettings,
    actor: str,
    port: int,
    target: str,
    headers: list[tuple[str, str]],
) -> None:
    """Relay a browser WebSocket to ``target`` on a port of an actor.

    The browser's WebSocket is accepted only once the actor has accepted
    its own, so a refusal reaches the browser as a refused upgrade.
    """
    subprotocols = [
        Subprotocol(value.strip())
        for value in websocket.headers.get('sec-websocket-protocol', '').split(',')
        if value.strip()
    ]
    try:
        stream, buffered = await open_tunnel(settings, actor, port)
    except RouterError as exc:
        _logger.warning(
            'substrate.websocket_tunnel_failed',
            extra={'actor': actor, 'port': port, 'error': str(exc)},
        )
        await websocket.close(code=CloseCode.TRY_AGAIN_LATER)
        return

    protocol = ClientProtocol(
        parse_uri(f'ws://{actor}:{port}{target}'),
        subprotocols=subprotocols or None,
        max_size=MAX_WEBSOCKET_MESSAGE_BYTES,
    )
    request = protocol.connect()
    for name, value in headers:
        request.headers[name] = value
    protocol.send_request(request)
    relay = _WebSocketRelay(websocket, stream, protocol)
    try:
        with anyio.fail_after(settings.connect_timeout):
            response = await relay.handshake(buffered)
        if response is None:
            await websocket.close(code=CloseCode.POLICY_VIOLATION)
            return
        await websocket.accept(
            subprotocol=response.headers.get('Sec-WebSocket-Protocol')
        )

        async def run(direction) -> None:
            try:
                await direction()
            except (
                WebSocketDisconnect,
                anyio.BrokenResourceError,
                anyio.ClosedResourceError,
                OSError,
                ssl.SSLError,
                RuntimeError,
            ):
                pass
            task_group.cancel_scope.cancel()

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run, relay.browser_to_actor)
            task_group.start_soon(run, relay.actor_to_browser)
    except TimeoutError:
        await websocket.close(code=CloseCode.TRY_AGAIN_LATER)
    finally:
        await stream.aclose()
