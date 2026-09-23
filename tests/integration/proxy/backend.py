"""Controlled transport peer; this is deliberately not an application emulator."""

import asyncio
import hashlib
import hmac
import os

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

app = FastAPI()
ROLE = os.environ['FIXTURE_ROLE']
PREFIX = '/api/automation' if ROLE == 'automation' else ''


@app.post(PREFIX + '/signed')
async def signed(request: Request):
    body = await request.body()
    expected = 'sha256=' + hmac.new(b'fixture-only', body, hashlib.sha256).hexdigest()
    valid = hmac.compare_digest(
        request.headers.get('x-hub-signature-256', ''), expected
    )
    return JSONResponse({'accepted': valid}, status_code=202 if valid else 401)


@app.post(PREFIX + '/upload')
async def upload(request: Request):
    body = await request.body()
    return {'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}


@app.get(PREFIX + '/redirect')
async def redirect(request: Request):
    response = RedirectResponse(str(request.url.replace(path=PREFIX + '/echo')))
    response.set_cookie('session', 'synthetic', secure=True, httponly=True)
    response.set_cookie('org', 'synthetic', secure=True, httponly=True)
    return response


@app.get(PREFIX + '/events')
async def events():
    async def generate():
        yield b'data: first\n\n'
        await asyncio.sleep(3)
        yield b'data: last\n\n'

    return StreamingResponse(generate(), media_type='text/event-stream')


@app.websocket(PREFIX + '/ws')
async def websocket(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            message = await websocket.receive_text()
            await websocket.send_text(f'{ROLE}:{message}')
    except WebSocketDisconnect:
        pass


@app.get('/{path:path}')
async def echo(request: Request, path: str):
    return {
        'role': ROLE,
        'path': request.url.path,
        'query': request.url.query,
        'scheme': request.url.scheme,
        'headers': dict(request.headers),
    }
