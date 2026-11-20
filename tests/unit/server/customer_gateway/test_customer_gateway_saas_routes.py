"""Relay paths as mounted in the REAL saas_server app (subprocess, like the users/me test).

ON: the prefixed relay paths reach the relay (401 invalid_token without a bearer, never
404/405) and the unprefixed /v1 paths are not the relay. OFF: no gateway route exists."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = textwrap.dedent(
    """
    import asyncio, json
    import httpx
    import saas_server

    PATHS = [
        ('POST', '/api/customer-gateway/v1/chat/completions'),
        ('GET', '/api/customer-gateway/v1/models'),
        ('GET', '/api/customer-gateway/token'),
        ('POST', '/v1/chat/completions'),
        ('GET', '/v1/models'),
    ]

    async def main():
        out = {'_paths': sorted(
            r.path for r in saas_server.app.routes
            if 'customer-gateway' in getattr(r, 'path', '')
            or getattr(r, 'path', '').startswith('/v1/')
        )}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=saas_server.app), base_url='http://testserver'
        ) as c:
            for method, path in PATHS:
                # A bogus bearer gets past the SaaS auth middleware to the route itself.
                r = await c.request(
                    method,
                    path,
                    json={} if method == 'POST' else None,
                    headers={'Authorization': 'Bearer not.a.token'},
                )
                try:
                    err = r.json().get('error')
                    code = err.get('code') if isinstance(err, dict) else err
                except Exception:
                    code = None
                out[f'{method} {path}'] = [r.status_code, code]
        print(json.dumps(out))

    asyncio.run(main())
    """
)


def _run(tmp_path, enabled: bool) -> dict:
    frontend = tmp_path / 'frontend' / 'build'
    frontend.mkdir(parents=True, exist_ok=True)
    (frontend / 'index.html').write_text('<html></html>')
    repo_root = Path(__file__).resolve().parents[4]
    env = os.environ.copy()
    env.update(
        PYTHONPATH=f'{repo_root}:{env.get("PYTHONPATH", "")}',
        OPENHANDS_SUPPRESS_BANNER='1',
        POSTHOG_CLIENT_KEY='test-posthog-key',
        SERVE_FRONTEND='false',
        FRONTEND_DIRECTORY=str(frontend),
        CUSTOMER_GATEWAY_RELAY_URL='https://app.example/api/customer-gateway/v1',
        CUSTOMER_GATEWAY_TOKEN_URL='https://app.example/api/customer-gateway/token',
    )
    if enabled:
        env['CUSTOMER_GATEWAY_ENABLED'] = 'true'
    else:
        env.pop('CUSTOMER_GATEWAY_ENABLED', None)
    result = subprocess.run(
        [sys.executable, '-c', SCRIPT],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.timeout(300)
def test_relay_mounted_under_configured_prefix_when_on(tmp_path):
    out = _run(tmp_path, enabled=True)
    assert out.pop('_paths') == [
        '/api/customer-gateway/token',
        '/api/customer-gateway/v1/chat/completions',
        '/api/customer-gateway/v1/models',
    ]
    assert out['POST /api/customer-gateway/v1/chat/completions'] == [
        401,
        'invalid_token',
    ]
    assert out['GET /api/customer-gateway/v1/models'] == [401, 'invalid_token']
    assert out['GET /api/customer-gateway/token'][0] in (401, 403)
    # The SDK saw this 405 when the relay was mounted at /v1/... instead.
    assert out['POST /v1/chat/completions'][0] == 405
    for path in ('POST /v1/chat/completions', 'GET /v1/models'):
        assert out[path][1] != 'invalid_token', path


@pytest.mark.timeout(300)
def test_no_gateway_routes_when_off(tmp_path):
    out = _run(tmp_path, enabled=False)
    assert out.pop('_paths') == []
    for key, (_status, code) in out.items():
        assert code != 'invalid_token', key
