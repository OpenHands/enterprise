"""Origin-root domain verification before MCP setup, without authentication."""

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from server.auth.external_mcp import mount_external_mcp

PATH = '/.well-known/openai-apps-challenge'


def client_with_spa(monkeypatch):
    monkeypatch.delenv('OPENHANDS_EXTERNAL_MCP_CLIENTS', raising=False)
    app = FastAPI()
    mount_external_mcp(app)

    @app.get('/{path:path}')
    async def frontend(path: str):
        return HTMLResponse('<html>OpenHands frontend</html>')

    return TestClient(app)


def test_exact_plaintext_token_before_frontend_and_without_oauth(monkeypatch):
    monkeypatch.setenv('OPENAI_APPS_CHALLENGE_TOKEN', 'test-domain-token_123')
    client = client_with_spa(monkeypatch)
    response = client.get(PATH, follow_redirects=False)
    assert response.status_code == 200
    assert response.content == b'test-domain-token_123'
    assert response.headers['content-type'] == 'text/plain; charset=utf-8'
    assert response.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in response.headers
    assert client.get('/').headers['content-type'].startswith('text/html')
    assert PATH not in client.get('/openapi.json').json()['paths']


@pytest.mark.parametrize('token', [None, '', ' \n\t'])
def test_missing_token_returns_404_instead_of_frontend(monkeypatch, token):
    if token is None:
        monkeypatch.delenv('OPENAI_APPS_CHALLENGE_TOKEN', raising=False)
    else:
        monkeypatch.setenv('OPENAI_APPS_CHALLENGE_TOKEN', token)
    response = client_with_spa(monkeypatch).get(PATH)
    assert response.status_code == 404
    assert 'OpenHands frontend' not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_invalid_mcp_configuration_does_not_block_verification(monkeypatch):
    monkeypatch.setenv('OPENAI_APPS_CHALLENGE_TOKEN', 'verification-before-oauth')
    monkeypatch.setenv('OPENHANDS_EXTERNAL_MCP_CLIENTS', 'not-json')
    app = FastAPI()
    mount_external_mcp(app)
    response = TestClient(app).get(PATH)
    assert response.status_code == 200
    assert response.text == 'verification-before-oauth'
