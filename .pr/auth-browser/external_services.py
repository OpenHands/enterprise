"""Synthetic remote GitHub/model and SMTP boundaries for local browser QA."""

import asyncio
import json
import threading
import time
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

WORK = Path(__file__).parent
STATE = json.loads((WORK / 'state.json').read_text())
EVENTS = WORK / 'external-events.jsonl'
MESSAGES = WORK / 'emails'
MESSAGES.mkdir(exist_ok=True)


def event(kind, **fields):
    with EVENTS.open('a') as stream:
        stream.write(json.dumps({'kind': kind, **fields}) + '\n')


class Endpoint(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/user':
            valid = self.headers.get('Authorization') in (
                'Bearer synthetic-github-token',
                'token synthetic-github-token',
            )
            event('provider_user', authorized=valid)
            return self.json(
                {
                    'id': 10101,
                    'login': 'auth-test-github',
                    'name': 'Synthetic GitHub User',
                    'email': 'github@auth-test.example',
                    'avatar_url': '',
                    'html_url': 'https://github.com/auth-test-github',
                }
                if valid
                else {'message': 'Bad credentials'},
                200 if valid else 401,
            )
        if self.path.startswith('/user/installations'):
            return self.json({'total_count': 0, 'installations': []})
        if self.path.startswith(('/user/repos', '/user/orgs', '/user/emails')):
            return self.json([])
        if self.path == '/v1/models':
            return self.json(
                {'object': 'list', 'data': [{'id': 'gpt-4o-mini', 'object': 'model'}]}
            )
        return self.json({'error': 'Unimplemented synthetic external endpoint'}, 404)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.path == '/graphql':
            return self.json(
                {
                    'data': {
                        'viewer': {
                            'repositories': {
                                'nodes': [],
                                'pageInfo': {'hasNextPage': False},
                            }
                        }
                    }
                }
            )
        if self.path == '/v1/chat/completions':
            data = json.loads(body)
            event(
                'llm_completion',
                model=data.get('model'),
                streaming=bool(data.get('stream')),
                message_count=len(data.get('messages', [])),
            )
            result = {
                'id': 'chatcmpl-auth-test-' + uuid4().hex[:12],
                'object': 'chat.completion',
                'created': int(time.time()),
                'model': 'gpt-4o-mini',
                'choices': [
                    {
                        'index': 0,
                        'message': {
                            'role': 'assistant',
                            'content': 'The isolated authentication test conversation is working.',
                        },
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {
                    'prompt_tokens': 100,
                    'completion_tokens': 12,
                    'total_tokens': 112,
                },
            }
            if data.get('stream'):
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                chunk = {
                    **result,
                    'object': 'chat.completion.chunk',
                    'choices': [
                        {
                            'index': 0,
                            'delta': result['choices'][0]['message'],
                            'finish_reason': None,
                        }
                    ],
                }
                end = {
                    **result,
                    'object': 'chat.completion.chunk',
                    'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                }
                self.wfile.write(
                    (
                        'data: '
                        + json.dumps(chunk)
                        + '\n\ndata: '
                        + json.dumps(end)
                        + '\n\ndata: [DONE]\n\n'
                    ).encode()
                )
                return
            return self.json(result)
        return self.json({})


async def smtp(reader, writer):
    async def send(line):
        writer.write((line + '\r\n').encode())
        await writer.drain()

    await send('220 auth-test.example ESMTP')
    while line := await reader.readline():
        command = line.decode(errors='replace').strip().split(' ', 1)[0].upper()
        if command in ('EHLO', 'HELO'):
            await send('250 auth-test.example')
        elif command in ('MAIL', 'RCPT', 'RSET', 'NOOP'):
            await send('250 OK')
        elif command == 'DATA':
            await send('354 End data with <CR><LF>.<CR><LF>')
            lines = []
            while (part := await reader.readline()) not in (b'.\r\n', b'.\n', b''):
                lines.append(part[1:] if part.startswith(b'..') else part)
            content = b''.join(lines)
            message = BytesParser(policy=policy.default).parsebytes(content)
            destination = MESSAGES / (uuid4().hex + '.eml')
            destination.write_bytes(content)
            destination.chmod(0o600)
            event(
                'smtp_delivery',
                subject=str(message['Subject']),
                recipient=str(message['To']),
                file=destination.name,
            )
            await send('250 Queued')
        elif command == 'QUIT':
            await send('221 Bye')
            break
        else:
            await send('502 Command not implemented')
    writer.close()
    await writer.wait_closed()


async def main():
    endpoint = ThreadingHTTPServer(('127.0.0.1', STATE['llm_port']), Endpoint)
    threading.Thread(target=endpoint.serve_forever, daemon=True).start()
    server = await asyncio.start_server(smtp, '127.0.0.1', STATE['smtp_port'])
    print('Synthetic provider/model and SMTP listeners ready', flush=True)
    async with server:
        await server.serve_forever()


asyncio.run(main())
