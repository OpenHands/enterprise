"""In-process stand-in for runtime-api's warm runtime config endpoints.

It mirrors runtime-api's challenge/response admin login (PBKDF2 over
``salt + challenge``) and its admin-JWT protected PUT/DELETE, so the code under
test exercises the real handshake rather than a canned response.
"""

import hashlib
import uuid

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

ADMIN_PASSWORD = 'runtime-api-admin-password'
API_KEY = 'runtime-api-key'
ITERATIONS = 1000


class Login(BaseModel):
    challenge: str
    hash: str


def create_fake_runtime_api() -> FastAPI:
    app = FastAPI()
    app.state.configs = {}
    app.state.challenges = {}
    app.state.tokens = set()
    app.state.logins = 0

    @app.get('/api/warm-runtime-configs')
    def list_configs(request: Request, x_api_key: str | None = Header(None)):
        if x_api_key != API_KEY:
            raise HTTPException(401, 'Invalid API key')
        return {
            'configs': [
                {'name': name, **config}
                for name, config in request.app.state.configs.items()
            ]
        }

    @app.get('/api/admin/challenge')
    def challenge(request: Request):
        challenge_id, salt = str(uuid.uuid4()), uuid.uuid4().hex
        request.app.state.challenges[challenge_id] = salt
        return {'challenge': challenge_id, 'salt': salt, 'iterations': ITERATIONS}

    @app.post('/api/admin/login')
    def login(request: Request, body: Login):
        salt = request.app.state.challenges.pop(body.challenge, None)
        if salt is None:
            raise HTTPException(400, 'Invalid or expired challenge')
        expected = hashlib.pbkdf2_hmac(
            'sha256',
            ADMIN_PASSWORD.encode(),
            (salt + body.challenge).encode(),
            ITERATIONS,
            dklen=32,
        ).hex()
        if body.hash != expected:
            raise HTTPException(401, 'Invalid admin password')
        token = f'token-{uuid.uuid4()}'
        request.app.state.tokens.add(token)
        request.app.state.logins += 1
        return {'token': token}

    def require_admin(request: Request, authorization: str | None):
        token = (authorization or '').removeprefix('Bearer ')
        if token not in request.app.state.tokens:
            raise HTTPException(401, 'Invalid admin token')

    @app.put('/api/admin/warm-runtime-configs/{name}')
    def save(
        name: str,
        request: Request,
        body: dict,
        authorization: str | None = Header(None),
    ):
        require_admin(request, authorization)
        if body.get('idle_seconds') is not None and body['idle_seconds'] <= 0:
            raise HTTPException(
                422,
                [{'loc': ['body', 'idle_seconds'], 'msg': 'must be greater than 0'}],
            )
        request.app.state.configs[name] = {**body, 'source': 'db'}
        return {'name': name, **request.app.state.configs[name]}

    @app.delete('/api/admin/warm-runtime-configs/{name}')
    def delete(name: str, request: Request, authorization: str | None = Header(None)):
        require_admin(request, authorization)
        if name not in request.app.state.configs:
            raise HTTPException(404, f'Configuration "{name}" not found')
        del request.app.state.configs[name]
        return {'message': 'deleted'}

    return app
