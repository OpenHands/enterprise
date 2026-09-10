#!/Users/jlaverty/dev/enterprise/.venv/bin/python
"""Start the real SDK agent behind a loopback HTTP/TLS transport for browser QA."""

import os
import signal
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
from pathlib import Path

WORK = Path(__file__).parent
args = sys.argv[1:]
index = args.index('--port') + 1
public_port = int(args[index])
with socket.socket() as reservation:
    reservation.bind(('127.0.0.1', 0))
    internal_port = reservation.getsockname()[1]
args[index] = str(internal_port)
child = subprocess.Popen(
    [str(WORK.parents[1] / '.venv/bin/python'), *args], env=os.environ
)
tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
tls.load_cert_chain(WORK / 'localhost.crt', WORK / 'localhost.key')


def copy(source, target):
    try:
        while data := source.recv(65536):
            target.sendall(data)
    except (OSError, ssl.SSLError):
        pass
    finally:
        try:
            target.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        downstream = self.request
        try:
            if downstream.recv(1, socket.MSG_PEEK) == b'\x16':
                downstream = tls.wrap_socket(downstream, server_side=True)
            with socket.create_connection(('127.0.0.1', internal_port)) as upstream:
                incoming = threading.Thread(
                    target=copy, args=(downstream, upstream), daemon=True
                )
                incoming.start()
                copy(upstream, downstream)
        except (OSError, ssl.SSLError):
            pass
        finally:
            downstream.close()


class Transport(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def terminate(*_args):
    child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
    raise SystemExit(0)


signal.signal(signal.SIGTERM, terminate)
signal.signal(signal.SIGINT, terminate)
try:
    with Transport(('127.0.0.1', public_port), Handler) as server:
        server.timeout = 1
        while child.poll() is None:
            server.handle_request()
finally:
    if child.poll() is None:
        child.terminate()
