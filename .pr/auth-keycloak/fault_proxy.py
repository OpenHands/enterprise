"""Loopback-only Keycloak backchannel proxy for temporary 503/429 failures."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

WORK = Path(__file__).resolve().parent
state = json.loads((WORK / 'state.json').read_text())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def do_PUT(self):
        self.forward()

    def do_DELETE(self):
        self.forward()

    def forward(self):
        fault_path = WORK / 'fault.json'
        fault = json.loads(fault_path.read_text()) if fault_path.exists() else {}
        content = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if fault.get('status') and '/protocol/openid-connect/token' in self.path:
            status = int(fault['status'])
            body = json.dumps({'error': 'temporarily_unavailable'}).encode()
            headers = {'Content-Type': 'application/json', 'Retry-After': '1'}
        else:
            try:
                response = httpx.request(
                    self.command,
                    state['kc_url'] + self.path,
                    content=content,
                    headers={
                        key: value
                        for key, value in self.headers.items()
                        if key.lower() not in ('host', 'connection', 'content-length')
                    },
                    timeout=3,
                )
                status, body = response.status_code, response.content
                headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower()
                    not in (
                        'connection',
                        'transfer-encoding',
                        'content-length',
                        'content-encoding',
                    )
                }
            except httpx.TransportError:
                status, body, headers = (
                    502,
                    b'{"error":"upstream_unavailable"}',
                    {'Content-Type': 'application/json'},
                )
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        with (WORK / 'proxy-events.jsonl').open('a') as log:
            log.write(
                json.dumps(
                    {
                        'method': self.command,
                        'path': self.path.split('?')[0],
                        'status': status,
                    }
                )
                + '\n'
            )


ThreadingHTTPServer(('127.0.0.1', state['proxy_port']), Handler).serve_forever()
