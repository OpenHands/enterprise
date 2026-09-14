from __future__ import annotations

import asyncio
import socket
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response


def reserve_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


class LocalAppServer(AbstractContextManager['LocalAppServer']):
    def __init__(self, app: FastAPI):
        self.port = reserve_port()
        self.base_url = f'http://127.0.0.1:{self.port}'
        self._server = uvicorn.Server(
            uvicorn.Config(app, host='127.0.0.1', port=self.port, log_level='warning')
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> 'LocalAppServer':
        self._thread.start()
        for _ in range(100):
            try:
                if httpx.get(f'{self.base_url}/health', timeout=0.1).is_success:
                    return self
            except httpx.HTTPError:
                pass
            threading.Event().wait(0.05)
        raise RuntimeError(f'test service did not become ready: {self.base_url}')

    def __exit__(self, *_args: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@dataclass
class ProviderState:
    calls: int = 0


def create_provider_app(state: ProviderState) -> FastAPI:
    app = FastAPI()

    @app.get('/health')
    async def health() -> dict[str, str]:
        return {'status': 'ok'}

    @app.post('/v1/chat/completions')
    async def completion() -> dict[str, Any]:
        state.calls += 1
        return {
            'id': f'budget-test-{state.calls}',
            'object': 'chat.completion',
            'created': 0,
            'model': 'budget-test-model',
            'choices': [
                {
                    'index': 0,
                    'finish_reason': 'stop',
                    'message': {'role': 'assistant', 'content': 'ok'},
                }
            ],
            'usage': {
                'prompt_tokens': 10,
                'completion_tokens': 5,
                'total_tokens': 15,
            },
        }

    @app.get('/test/requests')
    async def requests() -> dict[str, int]:
        return {'calls': state.calls}

    @app.post('/test/reset')
    async def reset() -> dict[str, int]:
        state.calls = 0
        return {'calls': state.calls}

    return app


@dataclass
class FailureRule:
    path: str
    status_code: int = 500
    remaining: int = 1


class ProxyState:
    def __init__(self, upstream_url: str):
        self.upstream_url = upstream_url.rstrip('/')
        self.failures: list[FailureRule] = []
        self.requests: list[str] = []
        self.lock = asyncio.Lock()

    async def consume_failure(self, path: str) -> FailureRule | None:
        async with self.lock:
            self.requests.append(path)
            for failure in self.failures:
                if failure.remaining and path.startswith(failure.path):
                    failure.remaining -= 1
                    return failure
        return None


def create_proxy_app(state: ProxyState) -> FastAPI:
    app = FastAPI()

    @app.get('/health')
    async def health() -> dict[str, str]:
        return {'status': 'ok'}

    @app.post('/test/fail-next')
    async def fail_next(rule: FailureRule) -> dict[str, Any]:
        async with state.lock:
            state.failures.append(rule)
        return rule.__dict__

    @app.post('/test/reset')
    async def reset() -> dict[str, str]:
        async with state.lock:
            state.failures.clear()
            state.requests.clear()
        return {'status': 'ok'}

    @app.get('/test/requests')
    async def requests() -> dict[str, list[str]]:
        async with state.lock:
            return {'paths': list(state.requests)}

    @app.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    async def forward(path: str, request: Request) -> Response:
        request_path = f'/{path}'
        failure = await state.consume_failure(request_path)
        if failure is not None:
            return Response('injected management failure', failure.status_code)

        body = await request.body()
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {'host', 'content-length'}
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                request.method,
                f'{state.upstream_url}{request_path}',
                params=request.query_params,
                content=body,
                headers=headers,
            )
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers={
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {'content-encoding', 'transfer-encoding'}
            },
            media_type=response.headers.get('content-type'),
        )

    return app
