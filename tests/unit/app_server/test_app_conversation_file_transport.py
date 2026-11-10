"""Byte-preserving workspace file transport: ``/file/download`` and ``/raw/{path}``.

The router runs in a real FastAPI app and talks to a fake agent server through
a real ``httpx.AsyncClient`` (``httpx.MockTransport``), so routing, path
decoding, headers and status mapping are all exercised. Only the conversation
and sandbox services are mocked.
"""

import hashlib
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
)
from openhands.app_server.app_conversation.app_conversation_router import (
    AGENT_SERVER,
    app_conversation_service_dependency,
    httpx_client_dependency,
    router,
    sandbox_service_dependency,
    sandbox_spec_service_dependency,
)
from openhands.app_server.sandbox.sandbox_models import SandboxStatus

RUNTIME_URL = 'http://runtime.test'
SESSION_KEY = 'sess-key-do-not-leak'

PNG = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + os.urandom(256)
PDF = b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n' + os.urandom(256) + b'\n%%EOF\n'
BINARY = bytes(range(256)) * 4

FILES = {
    '/workspace/enterprise/docs/logo.png': PNG,
    '/workspace/enterprise/docs/spec.pdf': PDF,
    '/workspace/enterprise/data.bin': BINARY,
    '/workspace/enterprise/empty.txt': b'',
    '/workspace/enterprise/site/index.html': b'<link href="style.css">',
    '/workspace/enterprise/site/style.css': b'body { color: red }',
    '/workspace/enterprise/r\xe9sum\xe9 "v1".txt': 'h\xe9'.encode(),
    '/tmp/report.png': PNG,
}


def _runtime(request: httpx.Request) -> httpx.Response:
    """Mimic the agent server's ``GET /api/file/download?path=``."""
    if request.headers.get('X-Session-API-Key') != SESSION_KEY:
        return httpx.Response(401, json={'detail': f'bad key {SESSION_KEY}'})
    assert request.url.path == '/api/file/download'
    path = request.url.params['path']
    if path in FILES:
        return httpx.Response(
            200,
            content=FILES[path],
            headers={'content-type': 'application/octet-stream'},
        )
    if path.rstrip('/') in {'/workspace/enterprise', '/workspace/enterprise/site'}:
        return httpx.Response(400, json={'detail': 'Path is not a file'})
    return httpx.Response(404, json={'detail': 'File not found'})


def _sandbox(status=SandboxStatus.RUNNING, with_url=True):
    return SimpleNamespace(
        id='sb-1',
        status=status,
        session_api_key=SESSION_KEY,
        sandbox_spec_id='spec-1',
        exposed_urls=[SimpleNamespace(name=AGENT_SERVER, url=RUNTIME_URL)]
        if with_url
        else [],
    )


class _Harness:
    def __init__(self, runtime=_runtime):
        self.conversation_id = uuid4()
        self.conversation = AppConversationInfo(
            id=self.conversation_id,
            created_by_user_id='owner',
            sandbox_id='sb-1',
            selected_repository='OpenHands/enterprise',
        )
        self.conversations = MagicMock()
        self.conversations.get_app_conversation = AsyncMock(
            side_effect=lambda cid: self.conversation
            if cid == self.conversation_id
            else None
        )
        self.sandboxes = MagicMock()
        self.sandboxes.get_sandbox = AsyncMock(return_value=_sandbox())
        self.specs = MagicMock()
        self.specs.resolve_existing_sandbox_spec = AsyncMock(
            return_value=SimpleNamespace(working_dir='/workspace')
        )
        self.runtime_calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.runtime_calls.append(request)
            return runtime(request)

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[app_conversation_service_dependency.dependency] = (
            lambda: self.conversations
        )
        app.dependency_overrides[sandbox_service_dependency.dependency] = (
            lambda: self.sandboxes
        )
        app.dependency_overrides[sandbox_spec_service_dependency.dependency] = (
            lambda: self.specs
        )
        app.dependency_overrides[httpx_client_dependency.dependency] = (
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        self.client = TestClient(app)

    def download(self, file_path, conversation_id=None, **params):
        cid = conversation_id or self.conversation_id
        return self.client.get(
            f'/app-conversations/{cid}/file/download',
            params={'file_path': file_path, **params},
        )

    def raw(self, path, conversation_id=None):
        cid = conversation_id or self.conversation_id
        return self.client.get(f'/app-conversations/{cid}/raw/{path}')


@pytest.fixture
def harness():
    return _Harness()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    'file_path, expected, media_type',
    [
        ('docs/logo.png', PNG, 'image/png'),
        ('/workspace/project/enterprise/docs/spec.pdf', PDF, 'application/pdf'),
        ('data.bin', BINARY, 'application/octet-stream'),
        ('/tmp/report.png', PNG, 'image/png'),
    ],
)
def test_download_round_trips_bytes_and_mime(harness, file_path, expected, media_type):
    response = harness.download(file_path)

    assert response.status_code == 200
    assert _sha(response.content) == _sha(expected)
    assert response.headers['content-type'] == media_type
    assert response.headers['content-disposition'].startswith('attachment; ')
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['cache-control'] == 'private, no-store'


def test_download_of_an_empty_file_succeeds(harness):
    response = harness.download('empty.txt')

    assert response.status_code == 200
    assert response.content == b''
    assert response.headers['content-type'] == 'text/plain; charset=utf-8'


def test_download_encodes_unsafe_file_names(harness):
    response = harness.download('r\xe9sum\xe9 "v1".txt')

    assert response.status_code == 200
    assert response.headers['content-disposition'] == (
        'attachment; filename="rsum v1.txt"; '
        "filename*=UTF-8''r%C3%A9sum%C3%A9%20%22v1%22.txt"
    )


def test_download_sends_the_session_key_only_to_the_runtime(harness):
    response = harness.download('docs/logo.png')

    assert harness.runtime_calls[0].headers['X-Session-API-Key'] == SESSION_KEY
    assert harness.runtime_calls[0].url.params['path'] == (
        '/workspace/enterprise/docs/logo.png'
    )
    assert SESSION_KEY not in str(response.headers)


@pytest.mark.parametrize(
    'setup, status_code, code',
    [
        (lambda h: None, 404, 'file_not_found'),
        (
            lambda h: setattr(h.sandboxes, 'get_sandbox', AsyncMock(return_value=None)),
            404,
            'sandbox_not_found',
        ),
        (
            lambda h: setattr(
                h.sandboxes,
                'get_sandbox',
                AsyncMock(return_value=_sandbox(SandboxStatus.PAUSED)),
            ),
            409,
            'sandbox_paused',
        ),
        (
            lambda h: setattr(
                h.sandboxes,
                'get_sandbox',
                AsyncMock(return_value=_sandbox(SandboxStatus.STARTING)),
            ),
            503,
            'sandbox_not_ready',
        ),
        (
            lambda h: setattr(
                h.sandboxes,
                'get_sandbox',
                AsyncMock(return_value=_sandbox(with_url=False)),
            ),
            503,
            'sandbox_not_ready',
        ),
    ],
)
def test_download_failures_are_distinguishable(harness, setup, status_code, code):
    setup(harness)

    response = harness.download('missing.txt')

    assert response.status_code == status_code
    assert response.json()['code'] == code
    assert response.headers['cache-control'] == 'no-store'


def test_download_of_a_directory_is_invalid_path(harness):
    response = harness.download('site')

    assert response.status_code == 400
    assert response.json()['code'] == 'invalid_path'


def test_download_of_an_unknown_repository_is_not_found(harness):
    response = harness.download('README.md', repository='other-repo')

    assert response.status_code == 404
    assert response.json()['code'] == 'repository_not_found'
    assert harness.runtime_calls == []


def test_conversation_of_another_user_is_not_found_and_never_reaches_runtime(
    harness,
):
    """The conversation service is scoped to the caller's user and org, so
    another tenant's conversation id resolves to nothing."""
    response = harness.download('docs/logo.png', conversation_id=uuid4())

    assert response.status_code == 404
    assert response.json()['code'] == 'conversation_not_found'
    assert harness.runtime_calls == []


def test_unreachable_runtime_is_503():
    def refuse(request):
        raise httpx.ConnectError(f'connection refused, key {SESSION_KEY}')

    response = _Harness(runtime=refuse).download('docs/logo.png')

    assert response.status_code == 503
    assert response.json() == {
        'code': 'sandbox_unreachable',
        'error': 'The sandbox did not respond.',
    }


@pytest.mark.parametrize('runtime_status', [401, 403, 500])
def test_runtime_errors_are_502_without_upstream_text(runtime_status):
    def fail(request):
        return httpx.Response(runtime_status, text=f'boom {SESSION_KEY}')

    response = _Harness(runtime=fail).download('docs/logo.png')

    assert response.status_code == 502
    assert response.json() == {
        'code': 'upstream_error',
        'error': f'Agent server error: {runtime_status}',
    }


def test_raw_serves_inline_without_scripts(harness):
    response = harness.raw('enterprise/site/index.html')

    assert response.status_code == 200
    assert response.content == FILES['/workspace/enterprise/site/index.html']
    assert response.headers['content-type'] == 'text/html; charset=utf-8'
    assert response.headers['content-disposition'].startswith('inline; ')
    assert response.headers['content-security-policy'] == (
        "sandbox allow-same-origin; frame-ancestors 'self'"
    )
    assert response.headers['x-content-type-options'] == 'nosniff'


def test_raw_is_a_stable_asset_base_for_relative_links(harness):
    """``style.css`` linked from ``.../raw/enterprise/site/index.html`` resolves
    to ``.../raw/enterprise/site/style.css``: the same endpoint and session."""
    page = httpx.URL(
        f'http://app.test/app-conversations/{harness.conversation_id}'
        '/raw/enterprise/site/index.html'
    )
    asset = page.join('style.css')

    response = harness.client.get(asset.path)

    assert response.status_code == 200
    assert response.content == b'body { color: red }'
    assert response.headers['content-type'] == 'text/css; charset=utf-8'


def test_raw_round_trips_binary_bytes(harness):
    response = harness.raw('enterprise/docs/logo.png')

    assert response.status_code == 200
    assert _sha(response.content) == _sha(PNG)
    assert response.headers['content-type'] == 'image/png'


@pytest.mark.parametrize(
    'path',
    [
        'enterprise/%2E%2E/%2E%2E/etc/passwd',
        'enterprise/%2E/data.bin',
        'enterprise//data.bin',
        'enterprise/site/',
        '%2Fetc%2Fpasswd',
        'enterprise%5C..%5Cdata.bin',
    ],
)
def test_raw_rejects_paths_that_escape_the_workspace(harness, path):
    response = harness.raw(path)

    assert response.status_code == 400
    assert response.json()['code'] == 'invalid_path'
    assert harness.runtime_calls == []


def test_raw_missing_file_is_404(harness):
    response = harness.raw('enterprise/nope.css')

    assert response.status_code == 404
    assert response.json()['code'] == 'file_not_found'
