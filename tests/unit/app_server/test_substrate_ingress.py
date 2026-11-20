"""Tests for the atenet-router tunnel: RouterTransport and bridge_websocket.

A fake router listens on localhost with mutual TLS, as atenet-router does. It
answers a CONNECT that names the actor in ``ate-target-actor``, then plays the
actor: plain HTTP that echoes the request, and a WebSocket that echoes
messages.
"""

import asyncio
import datetime
import json
import socket
import ssl
from dataclasses import dataclass, field

import httpx
import pytest
import uvicorn
import websockets
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket
from websockets.frames import Opcode
from websockets.http11 import Request
from websockets.server import ServerProtocol

from openhands.app_server.sandbox.substrate.ingress import (
    RouterError,
    RouterSettings,
    RouterTransport,
    actor_url,
    bridge_websocket,
    open_tunnel,
)

ATESPACE = 'openhands'
ACTOR = 'oh-1'
SERVER_NAME = 'atenet-router.ate-system.svc'
CLIENT_SPIFFE_ID = 'spiffe://cluster.local/ns/openhands/sa/openhands'


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _pem_key(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _cert(subject_key, issuer_key, issuer_name, san, ca=False) -> x509.Certificate:
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(
            issuer_name
            if ca
            else x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'test')])
        )
        .issuer_name(issuer_name)
        .public_key(subject_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(subject_key.public_key()), False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=not ca,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=False,
                decipher_only=False,
            ),
            True,
        )
    )
    if san is not None:
        builder = builder.add_extension(x509.SubjectAlternativeName([san]), False)
    return builder.sign(issuer_key, hashes.SHA256())


@pytest.fixture(scope='module')
def pki(tmp_path_factory):
    """A CA, the router's serving certificate and the app's client bundle."""
    directory = tmp_path_factory.mktemp('pki')
    ca_key = _key()
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'test-ca')])
    ca = _cert(ca_key, ca_key, ca_name, None, ca=True)
    server_key = _key()
    server = _cert(server_key, ca_key, ca_name, x509.DNSName(SERVER_NAME))
    client_key = _key()
    client = _cert(
        client_key,
        ca_key,
        ca_name,
        x509.UniformResourceIdentifier(CLIENT_SPIFFE_ID),
    )
    pem = serialization.Encoding.PEM
    (directory / 'ca.pem').write_bytes(ca.public_bytes(pem))
    (directory / 'server.pem').write_bytes(
        _pem_key(server_key) + server.public_bytes(pem)
    )
    # Key first, then the chain, as a podCertificate credential bundle is.
    (directory / 'client.pem').write_bytes(
        _pem_key(client_key) + client.public_bytes(pem)
    )
    return directory


# ---------------------------------------------------------------------------
# The fake router and actor
# ---------------------------------------------------------------------------


@dataclass
class FakeRouter:
    port: int = 0
    connects: list[tuple[str, str | None]] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)
    refuse_with: str | None = None
    server: asyncio.Server | None = None


async def _read_head(reader: asyncio.StreamReader) -> tuple[str, dict[str, str]]:
    head = await reader.readuntil(b'\r\n\r\n')
    lines = head.decode('latin-1').split('\r\n')
    headers = {}
    for line in lines[1:]:
        if ':' in line:
            name, value = line.split(':', 1)
            headers[name.strip().lower()] = value.strip()
    return lines[0], headers


async def _serve_websocket(reader, writer, head: bytes) -> None:
    protocol = ServerProtocol()
    protocol.receive_data(head)
    request = next(e for e in protocol.events_received() if isinstance(e, Request))
    protocol.send_response(protocol.accept(request))
    for data in protocol.data_to_send():
        writer.write(data)
    # One message in two frames, to check the relay reassembles it.
    protocol.send_text(b'hel', fin=False)
    protocol.send_continuation(b'lo', fin=True)
    for data in protocol.data_to_send():
        writer.write(data)
    while True:
        data = await reader.read(65536)
        if not data:
            return
        protocol.receive_data(data)
        for event in protocol.events_received():
            if event.opcode == Opcode.TEXT:
                protocol.send_text(b'echo:' + event.data)
            elif event.opcode == Opcode.BINARY:
                protocol.send_binary(event.data[::-1])
        for data in protocol.data_to_send():
            writer.write(data)
        await writer.drain()
        if protocol.close_code is not None:
            return


async def _handle(router: FakeRouter, reader, writer) -> None:
    try:
        request_line, headers = await _read_head(reader)
        router.connects.append(
            (request_line.split(' ')[1], headers.get('ate-target-actor'))
        )
        if router.refuse_with:
            writer.write(f'HTTP/1.1 {router.refuse_with}\r\n\r\n'.encode())
            await writer.drain()
            return
        writer.write(b'HTTP/1.1 200 OK\r\n\r\n')
        await writer.drain()
        while True:
            head = await reader.readuntil(b'\r\n\r\n')
            request_line, headers = await _read_head(
                _reader_with(head)  # parse the bytes already read
            )
            if headers.get('upgrade', '').lower() == 'websocket':
                await _serve_websocket(reader, writer, head)
                return
            body = await reader.readexactly(int(headers.get('content-length', 0)))
            method, target, _ = request_line.split(' ')
            router.requests.append(
                {'method': method, 'target': target, 'headers': headers, 'body': body}
            )
            payload = json.dumps({'target': target, 'body': body.decode()}).encode()
            writer.write(
                b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
                + f'Content-Length: {len(payload)}\r\n\r\n'.encode()
                + payload
            )
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
        pass
    finally:
        writer.close()


def _reader_with(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


@pytest.fixture
async def router(pki):
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=pki / 'ca.pem')
    context.load_cert_chain(pki / 'server.pem')
    context.verify_mode = ssl.CERT_REQUIRED
    fake = FakeRouter()
    fake.server = await asyncio.start_server(
        lambda r, w: _handle(fake, r, w), '127.0.0.1', 0, ssl=context
    )
    fake.port = fake.server.sockets[0].getsockname()[1]
    yield fake
    fake.server.close()


def _settings(pki, router: FakeRouter, cert: str = 'client.pem') -> RouterSettings:
    return RouterSettings(
        address=f'127.0.0.1:{router.port}',
        atespace=ATESPACE,
        cert_file=str(pki / cert),
        ca_file=str(pki / 'ca.pem'),
        server_name=SERVER_NAME,
        connect_timeout=5,
    )


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_goes_through_a_tunnel_to_the_actor(pki, router):
    async with httpx.AsyncClient(
        transport=RouterTransport(_settings(pki, router))
    ) as client:
        response = await client.post(
            actor_url(ACTOR, 8000, '/api/init?x=1'), content=b'{"a": 1}'
        )

    assert response.json() == {'target': '/api/init?x=1', 'body': '{"a": 1}'}
    assert router.connects == [(f'{ACTOR}:8000', f'{ATESPACE}/{ACTOR}')]


@pytest.mark.asyncio
async def test_tunnels_are_reused_per_actor_and_port(pki, router):
    async with httpx.AsyncClient(
        transport=RouterTransport(_settings(pki, router))
    ) as client:
        await client.get(actor_url(ACTOR, 8000, '/a'))
        await client.get(actor_url(ACTOR, 8000, '/b'))
        await client.get(actor_url(ACTOR, 8001, '/c'))
        await client.get(actor_url('oh-2', 8000, '/d'))

    assert router.connects == [
        (f'{ACTOR}:8000', f'{ATESPACE}/{ACTOR}'),
        (f'{ACTOR}:8001', f'{ATESPACE}/{ACTOR}'),
        ('oh-2:8000', f'{ATESPACE}/oh-2'),
    ]


@pytest.mark.asyncio
async def test_refused_tunnel_is_a_connect_error(pki, router):
    router.refuse_with = '503 Service Unavailable'

    async with httpx.AsyncClient(
        transport=RouterTransport(_settings(pki, router))
    ) as client:
        with pytest.raises(httpx.ConnectError, match='503 Service Unavailable'):
            await client.get(actor_url(ACTOR, 8000, '/alive'))


@pytest.mark.asyncio
async def test_open_tunnel_reports_the_status(pki, router):
    router.refuse_with = '404 Not Found'

    with pytest.raises(RouterError) as exc_info:
        await open_tunnel(_settings(pki, router), ACTOR, 8000)

    assert exc_info.value.status == 404


@pytest.mark.asyncio
async def test_router_must_present_the_expected_name(pki, router):
    settings = _settings(pki, router)
    settings = RouterSettings(**{**settings.__dict__, 'server_name': 'other.svc'})

    with pytest.raises(RouterError, match='Could not reach atenet-router'):
        await open_tunnel(settings, ACTOR, 8000)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture
async def relay_app(pki, router):
    async def relay(websocket: WebSocket) -> None:
        await bridge_websocket(
            websocket,
            _settings(pki, router),
            ACTOR,
            8000,
            '/sockets/events?session_api_key=k',
            [('origin', 'https://app.example.com')],
        )

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            Starlette(routes=[WebSocketRoute('/ws', relay)]),
            port=port,
            log_level='warning',
        )
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    yield f'ws://127.0.0.1:{port}/ws'
    server.should_exit = True
    await task


@pytest.mark.asyncio
async def test_websocket_messages_are_relayed_both_ways(relay_app, router):
    async with websockets.connect(relay_app) as ws:
        assert await ws.recv() == 'hello'
        await ws.send('ping')
        assert await ws.recv() == 'echo:ping'
        await ws.send(b'abc')
        assert await ws.recv() == b'cba'

    assert router.connects == [(f'{ACTOR}:8000', f'{ATESPACE}/{ACTOR}')]


@pytest.mark.asyncio
async def test_refused_tunnel_refuses_the_browser(relay_app, router):
    router.refuse_with = '503 Service Unavailable'

    with pytest.raises(websockets.InvalidStatus):
        async with websockets.connect(relay_app):
            pass
