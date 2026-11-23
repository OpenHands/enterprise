"""In-process stand-in for runtime-api's warm runtime config endpoints.

Like runtime-api, it authenticates every request with ``X-API-Key``.
"""

from fastapi import FastAPI, Header, HTTPException, Request

API_KEY = 'runtime-api-key'


def create_fake_runtime_api() -> FastAPI:
    app = FastAPI()
    app.state.configs = {}

    def require_api_key(x_api_key: str | None):
        if x_api_key != API_KEY:
            raise HTTPException(401, 'Invalid API key')

    @app.get('/api/warm-runtime-configs')
    def list_configs(request: Request, x_api_key: str | None = Header(None)):
        require_api_key(x_api_key)
        return {
            'configs': [
                {'name': name, **config}
                for name, config in request.app.state.configs.items()
            ]
        }

    @app.put('/api/admin/warm-runtime-configs/{name}')
    def save(
        name: str,
        request: Request,
        body: dict,
        x_api_key: str | None = Header(None),
    ):
        require_api_key(x_api_key)
        if body.get('idle_seconds') is not None and body['idle_seconds'] <= 0:
            raise HTTPException(
                422,
                [{'loc': ['body', 'idle_seconds'], 'msg': 'must be greater than 0'}],
            )
        request.app.state.configs[name] = {**body, 'source': 'db'}
        return {'name': name, **request.app.state.configs[name]}

    @app.delete('/api/admin/warm-runtime-configs/{name}')
    def delete(name: str, request: Request, x_api_key: str | None = Header(None)):
        require_api_key(x_api_key)
        if name not in request.app.state.configs:
            raise HTTPException(404, f'Configuration "{name}" not found')
        del request.app.state.configs[name]
        return {'message': 'deleted'}

    return app
