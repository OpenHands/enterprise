"""Tests for ``POST /api/v1/settings/profiles/{name}/validate``.

The provider is a local OpenAI-compatible HTTP server, so each check runs the
real LiteLLM call path and the test can see which credential reached it.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.app_server.app import app
from openhands.app_server.file_store import get_file_store
from openhands.app_server.settings import llm_profile_validation
from openhands.app_server.settings.file_settings_store import FileSettingsStore
from openhands.app_server.settings.provider_connections import (
    ProviderConnection,
    ProviderConnections,
)
from openhands.app_server.settings.settings_models import Settings
from openhands.sdk.llm import LLM
from openhands.sdk.settings import OpenHandsAgentSettings
from tests.unit.app_server.test_profiles_api import _MockUserAuth

_OK_COMPLETION = {
    'id': 'chatcmpl-1',
    'object': 'chat.completion',
    'created': 0,
    'model': 'test-model',
    'choices': [
        {
            'index': 0,
            'message': {'role': 'assistant', 'content': 'pong'},
            'finish_reason': 'stop',
        }
    ],
    'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
}


class _FakeProvider:
    """OpenAI-compatible endpoint that records requests and answers per ``mode``."""

    def __init__(self) -> None:
        self.mode = 'ok'
        self.requests: list[dict] = []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get('Content-Length', 0))
                provider.requests.append(
                    {
                        'path': self.path,
                        'authorization': self.headers.get('Authorization'),
                        'body': json.loads(self.rfile.read(length) or b'{}'),
                    }
                )
                if provider.mode == 'slow':
                    time.sleep(3)
                if provider.mode == 'unauthorized':
                    key = (self.headers.get('Authorization') or '').removeprefix(
                        'Bearer '
                    )
                    self._send(
                        401,
                        {
                            'error': {
                                'message': f'Incorrect API key provided: {key}',
                                'type': 'invalid_request_error',
                                'code': 'invalid_api_key',
                            }
                        },
                    )
                elif provider.mode == 'not_found':
                    self._send(
                        404,
                        {
                            'error': {
                                'message': 'The model does not exist',
                                'type': 'invalid_request_error',
                                'code': 'model_not_found',
                            }
                        },
                    )
                else:
                    self._send(200, _OK_COMPLETION)

            def _send(self, code: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.base_url = f'http://127.0.0.1:{self.server.server_port}/v1'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def keys_seen(self) -> list[str | None]:
        return [r['authorization'] for r in self.requests]


@pytest.fixture
def provider():
    fake = _FakeProvider()
    fake.thread.start()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    llm_profile_validation._recent_checks.clear()
    yield
    llm_profile_validation._recent_checks.clear()


@pytest.fixture
def allow_local_targets():
    with patch.object(llm_profile_validation, '_ALLOW_LOCAL_TARGETS', True):
        yield


class _AuthWithConnections(_MockUserAuth):
    connections = ProviderConnections()

    async def get_provider_connections(self) -> ProviderConnections:
        return self.connections


@pytest.fixture
def settings_store(tmp_path: Path) -> FileSettingsStore:
    return FileSettingsStore(get_file_store('local', str(tmp_path)))


@pytest.fixture
def auth(settings_store):
    return _AuthWithConnections(settings_store)


@pytest.fixture
def client(auth, settings_store):
    with (
        patch.dict(
            os.environ,
            {'SESSION_API_KEY': '', 'ALLOW_SHORT_CONTEXT_WINDOWS': 'true'},
            clear=False,
        ),
        patch('openhands.app_server.utils.dependencies._SESSION_API_KEY', None),
        patch(
            'openhands.app_server.user_auth.default_user_auth.DefaultUserAuth.get_instance',
            AsyncMock(return_value=auth),
        ),
    ):
        yield TestClient(app)


async def _seed(store: FileSettingsStore, base_url: str) -> Settings:
    settings = Settings(
        agent_settings=OpenHandsAgentSettings(
            llm=LLM(
                model='openai/test-model',
                base_url=base_url,
                api_key=SecretStr('sk-current'),
            ),
        ),
    )
    settings.llm_profiles.save(
        'saved',
        LLM(
            model='openai/test-model',
            base_url=base_url,
            api_key=SecretStr('sk-profile'),
        ),
    )
    await store.store(settings)
    return settings


def _validate(client: TestClient, body: dict, name: str = 'draft'):
    return client.post(f'/api/v1/settings/profiles/{name}/validate', json=body)


@pytest.mark.asyncio
@pytest.mark.usefixtures('allow_local_targets')
async def test_valid_draft_pings_once_and_persists_nothing(
    client, settings_store, provider
):
    await _seed(settings_store, provider.base_url)
    before = (await settings_store.load()).model_dump(
        mode='json', context={'expose_secrets': True}
    )

    response = _validate(
        client, {'llm': {'model': 'openai/test-model', 'api_key': 'sk-typed'}}
    )

    assert response.status_code == 200
    assert response.json() == {'valid': True}
    assert provider.keys_seen == ['Bearer sk-typed']
    assert provider.requests[0]['path'] == '/v1/chat/completions'
    assert provider.requests[0]['body']['max_tokens'] == 1
    after = (await settings_store.load()).model_dump(
        mode='json', context={'expose_secrets': True}
    )
    assert after == before


@pytest.mark.asyncio
@pytest.mark.usefixtures('allow_local_targets')
async def test_auth_failure_is_invalid_and_redacts_key(
    client, settings_store, provider
):
    await _seed(settings_store, provider.base_url)
    provider.mode = 'unauthorized'

    response = _validate(client, {'llm': {'api_key': 'sk-wrong-secret-value'}})

    assert response.status_code == 200
    body = response.json()
    assert body['valid'] is False
    assert body['error']['type'] == 'LLMAuthenticationError'
    assert 'sk-wrong-secret-value' not in response.text
    assert provider.keys_seen == ['Bearer sk-wrong-secret-value']


@pytest.mark.asyncio
@pytest.mark.usefixtures('allow_local_targets')
async def test_unknown_model_is_invalid(client, settings_store, provider):
    await _seed(settings_store, provider.base_url)
    provider.mode = 'not_found'

    response = _validate(client, {'llm': {'model': 'openai/no-such-model'}})

    assert response.status_code == 200
    assert response.json()['valid'] is False
    assert provider.requests[0]['body']['model'] == 'no-such-model'


@pytest.mark.asyncio
@pytest.mark.usefixtures('allow_local_targets')
async def test_timeout_gives_no_verdict(client, settings_store, provider):
    await _seed(settings_store, provider.base_url)
    provider.mode = 'slow'

    started = time.monotonic()
    response = _validate(client, {'llm': {'timeout': 1}})

    assert response.status_code == 422
    assert 'LLMTimeoutError' in response.json()['detail']
    assert 'valid' not in response.json()
    assert len(provider.requests) == 1
    assert time.monotonic() - started < 3


@pytest.mark.asyncio
@pytest.mark.usefixtures('allow_local_targets')
async def test_blank_key_uses_settings_key(client, settings_store, provider):
    await _seed(settings_store, provider.base_url)

    response = _validate(client, {'llm': {}}, name='saved')

    assert response.json() == {'valid': True}
    assert provider.keys_seen == ['Bearer sk-current']


@pytest.mark.asyncio
@pytest.mark.usefixtures('allow_local_targets')
async def test_preserve_existing_api_key_uses_stored_profile_key(
    client, settings_store, provider
):
    await _seed(settings_store, provider.base_url)

    response = _validate(
        client, {'llm': {}, 'preserve_existing_api_key': True}, name='saved'
    )

    assert response.json() == {'valid': True}
    assert provider.keys_seen == ['Bearer sk-profile']


@pytest.mark.asyncio
@pytest.mark.usefixtures('allow_local_targets')
@pytest.mark.parametrize(
    'body',
    [
        {'llm': {}},
        {'llm': {'model': 'anthropic/claude-sonnet-4-5', 'base_url': None}},
        {'llm': {}, 'preserve_existing_api_key': True},
        {'llm': {'api_key': 'sk-typed'}, 'preserve_existing_api_key': True},
    ],
)
async def test_stored_key_is_not_sent_to_another_endpoint(
    client, settings_store, provider, body
):
    await _seed(settings_store, 'https://api.openai.com/v1')
    if 'base_url' not in body['llm']:
        body['llm']['base_url'] = provider.base_url

    response = _validate(client, body, name='saved')

    assert response.status_code == 422
    assert 'different provider or base URL' in response.json()['detail']
    assert provider.requests == []


@pytest.mark.asyncio
@pytest.mark.usefixtures('allow_local_targets')
async def test_typed_key_goes_to_any_endpoint(client, settings_store, provider):
    await _seed(settings_store, 'https://api.openai.com/v1')

    response = _validate(
        client, {'llm': {'base_url': provider.base_url, 'api_key': 'sk-typed'}}
    )

    assert response.json() == {'valid': True}
    assert provider.keys_seen == ['Bearer sk-typed']


@pytest.mark.asyncio
@pytest.mark.usefixtures('allow_local_targets')
async def test_linked_provider_connection_supplies_key_and_base_url(
    client, settings_store, auth, provider
):
    await _seed(settings_store, 'https://unused.example.com/v1')
    auth.connections = ProviderConnections(
        connections={
            'conn-1': ProviderConnection(
                id='conn-1',
                display_name='Gateway',
                api_key=SecretStr('sk-connection'),
                base_url=provider.base_url,
            )
        }
    )

    response = _validate(
        client, {'llm': {'provider_connection_id': 'conn-1', 'api_key': 'sk-typed'}}
    )

    assert response.json() == {'valid': True}
    assert provider.keys_seen == ['Bearer sk-connection']


@pytest.mark.asyncio
async def test_missing_provider_connection_is_422(client, settings_store, provider):
    await _seed(settings_store, provider.base_url)

    response = _validate(client, {'llm': {'provider_connection_id': 'gone'}})

    assert response.status_code == 422
    assert 'gone' in response.json()['detail']
    assert provider.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'base_url',
    [
        'http://127.0.0.1:11434/v1',
        'http://169.254.169.254/latest',
        'http://[::ffff:127.0.0.1]/v1',
        'file:///etc/passwd',
    ],
)
async def test_unsafe_base_url_is_refused(client, settings_store, base_url):
    await _seed(settings_store, 'https://api.openai.com/v1')

    response = _validate(client, {'llm': {'base_url': base_url}})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_managed_model_is_not_checked(client, settings_store):
    await _seed(settings_store, 'https://api.openai.com/v1')

    response = _validate(
        client, {'llm': {'model': 'openhands/claude-sonnet-4-5', 'base_url': None}}
    )

    assert response.status_code == 422
    assert 'Managed' in response.json()['detail']


@pytest.mark.asyncio
async def test_subscription_profile_is_not_checked(client, settings_store):
    await _seed(settings_store, 'https://api.openai.com/v1')

    response = _validate(
        client, {'llm': {'model': 'openai/gpt-5.1-codex', 'auth_type': 'subscription'}}
    )

    assert response.status_code == 422
    assert 'Subscription' in response.json()['detail']


@pytest.mark.asyncio
async def test_rate_limit_per_user(client, settings_store):
    await _seed(settings_store, 'https://api.openai.com/v1')
    body = {'llm': {'base_url': 'http://127.0.0.1/v1'}}

    statuses = [
        _validate(client, body).status_code
        for _ in range(llm_profile_validation.RATE_LIMIT_REQUESTS + 1)
    ]

    assert statuses[:-1] == [422] * llm_profile_validation.RATE_LIMIT_REQUESTS
    assert statuses[-1] == 429


async def _seed_without_key(store: FileSettingsStore, base_url: str) -> None:
    await store.store(
        Settings(
            agent_settings=OpenHandsAgentSettings(
                llm=LLM(model='openai/test-model', base_url=base_url)
            )
        )
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures('allow_local_targets')
async def test_keyless_draft_never_sends_server_env_key(
    client, settings_store, provider
):
    await _seed_without_key(settings_store, provider.base_url)

    with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-POD-ENV-SECRET'}):
        response = _validate(client, {'llm': {'model': 'openai/test-model'}})

    assert response.status_code == 422
    assert 'without an API key' in response.json()['detail']
    assert 'sk-POD-ENV-SECRET' not in response.text
    assert provider.requests == []


_SERVER_AWS_ENV = {
    'AWS_ACCESS_KEY_ID': 'AKIAPODENVKEY',
    'AWS_SECRET_ACCESS_KEY': 'pod-env-aws-secret',
    'AWS_BEARER_TOKEN_BEDROCK': 'pod-env-bedrock-token',
}


@pytest.mark.asyncio
@pytest.mark.usefixtures('allow_local_targets')
@pytest.mark.parametrize(
    'draft',
    [
        {
            'model': 'bedrock/anthropic.claude-3-haiku-20240307-v1:0',
            'aws_region_name': 'us-east-1',
        },
        {'model': 'anthropic.claude-3-haiku-20240307-v1:0', 'api_key': 'sk-typed'},
        {'model': 'vertex_ai/gemini-2.5-pro', 'api_key': 'sk-typed'},
        {'model': 'sagemaker/my-endpoint', 'api_key': 'sk-typed'},
    ],
)
async def test_cloud_identity_providers_are_not_checked(
    client, settings_store, provider, draft
):
    await _seed_without_key(settings_store, provider.base_url)

    with patch.dict(os.environ, _SERVER_AWS_ENV):
        response = _validate(
            client,
            {'llm': {**draft, 'aws_bedrock_runtime_endpoint': provider.base_url}},
        )

    assert response.status_code == 422
    assert 'Bedrock, SageMaker and Vertex AI' in response.json()['detail']
    assert provider.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'endpoint', ['http://127.0.0.1:9/v1', 'http://169.254.169.254', 'ftp://x.test']
)
async def test_unsafe_bedrock_runtime_endpoint_is_refused(
    client, settings_store, endpoint
):
    await _seed(settings_store, 'https://api.openai.com/v1')

    response = _validate(
        client,
        {
            'llm': {
                'model': 'bedrock/anthropic.claude-3-haiku-20240307-v1:0',
                'base_url': None,
                'aws_access_key_id': 'AKIATYPED',
                'aws_secret_access_key': 'typed-secret',
                'aws_bedrock_runtime_endpoint': endpoint,
            }
        },
    )

    assert response.status_code == 422
    assert response.json()['detail'].startswith('Bedrock runtime endpoint')


def test_error_message_drops_traceback_and_is_capped():
    llm = LLM(model='openai/test-model', api_key=SecretStr('sk-secret'))
    with_traceback = Exception(
        "litellm.APIConnectionError: 'output' sk-secret\n"
        'Traceback (most recent call last):\n'
        '  File "/app/.venv/lib/python3.13/site-packages/litellm/main.py", line 682'
    )
    too_long = Exception('x' * 2000)

    message = llm_profile_validation._error_message(with_traceback, llm)
    capped = llm_profile_validation._error_message(too_long, llm)

    assert message.startswith("litellm.APIConnectionError: 'output'")
    assert 'Traceback' not in message
    assert 'site-packages' not in message
    assert 'sk-secret' not in message
    assert len(capped) == llm_profile_validation.MAX_ERROR_MESSAGE_CHARS
    assert capped.endswith('...')
