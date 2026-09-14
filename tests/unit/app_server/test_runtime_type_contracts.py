"""Wire compatibility and redaction at typed sandbox boundaries."""

import httpx
import pytest
from docker.types import HostConfig
from pydantic import JsonValue, SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.agent_server.utils import utc_now
from openhands.app_server.sandbox.configured_sandbox_spec_service import (
    ConfiguredSandboxSpecService,
)
from openhands.app_server.sandbox.remote_sandbox_service import (
    RemoteSandboxService,
    StoredRemoteSandbox,
)
from openhands.app_server.sandbox.runtime_api_models import (
    RuntimeInfo,
    parse_runtime_batch,
)
from openhands.app_server.sandbox.sandbox_models import SandboxStatus
from openhands.app_server.sandbox.sandbox_provider_config import (
    DockerLaunchOptions,
    DockerLaunchSpec,
    SandboxProviderConfig,
)
from openhands.app_server.user.specifiy_user_context import SpecifyUserContext
from openhands.app_server.utils.litellm_integration import (
    LiteLLMIntegrationDisabled,
    validate_agent_llm_payload,
    validate_agent_llms,
)
from openhands.sdk.llm import LLM


@pytest.mark.parametrize(
    'content',
    [
        b'{"session_api_key":"private-session-key"}',
        b'{"session_api_key":"private-session-key",',
    ],
)
def test_invalid_batch_error_does_not_include_credentials(content: bytes) -> None:
    with pytest.raises(ValidationError) as exc:
        parse_runtime_batch(content)
    assert 'private-session-key' not in str(exc.value)


def test_runtime_representation_does_not_include_credentials() -> None:
    runtime = RuntimeInfo(session_api_key='private-session-key')
    assert 'private-session-key' not in repr(runtime)
    assert runtime.session_api_key == 'private-session-key'


def _remote_service(
    client: httpx.AsyncClient, session: AsyncSession
) -> RemoteSandboxService:
    return RemoteSandboxService(
        sandbox_spec_service=ConfiguredSandboxSpecService(
            SandboxProviderConfig(provider='runtime_api')
        ),
        api_url='https://runtime.invalid',
        api_key='test-api-key',
        web_url=None,
        resource_factor=1,
        runtime_class=None,
        start_sandbox_timeout=30,
        max_num_sandboxes=5,
        user_context=SpecifyUserContext('owner'),
        httpx_client=client,
        db_session=session,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body, statuses',
    [
        ([None, None], [SandboxStatus.MISSING, SandboxStatus.MISSING]),
        ([None], [SandboxStatus.UNKNOWN, SandboxStatus.UNKNOWN]),
        (
            [{'session_id': 'b', 'status': 'paused'}, None],
            [SandboxStatus.UNKNOWN, SandboxStatus.PAUSED],
        ),
        (
            [{'session_id': 'a', 'status': 'running'}, None],
            [SandboxStatus.UNKNOWN, SandboxStatus.MISSING],
        ),
        (
            [{'session_id': 'a', 'status': 'paused'}, {'session_id': 'a'}],
            [SandboxStatus.UNKNOWN, SandboxStatus.UNKNOWN],
        ),
        (
            [{'session_id': 'a', 'session_api_key': 123}, None],
            [SandboxStatus.UNKNOWN, SandboxStatus.UNKNOWN],
        ),
    ],
)
async def test_runtime_batch_requires_confirmed_absence(
    body: JsonValue, statuses: list[SandboxStatus]
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get_list('ids') == ['a', 'b']
        return httpx.Response(200, json=body)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client,
        AsyncSession() as session,
    ):
        service = _remote_service(client, session)
        runtimes = await service._get_runtimes_batch(['a', 'b'])
        actual = []
        for sandbox_id in ('a', 'b'):
            stored = StoredRemoteSandbox(
                id=sandbox_id,
                created_by_user_id='owner',
                sandbox_spec_id='python',
                created_at=utc_now(),
            )
            info = service._to_sandbox_info(
                stored, runtimes.get(sandbox_id, RuntimeInfo())
            )
            actual.append(info.status)
            if info.status == SandboxStatus.UNKNOWN:
                assert info.session_api_key is None
                assert info.exposed_urls is None
        assert actual == statuses


@pytest.mark.asyncio
@pytest.mark.parametrize('status_code', [404, 500, 503])
async def test_runtime_outage_is_not_confirmed_absence(status_code: int) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={'error': 'test failure'})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client,
        AsyncSession() as session,
    ):
        runtime = await _remote_service(client, session)._read_runtime('a')
        if status_code == 404:
            assert runtime is None
        else:
            assert runtime is not None
            assert runtime.status is None


def test_gateway_guard_handles_sdk_models_and_nested_tool_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('ENABLE_LITELLM', 'false')
    validate_agent_llm_payload({'properties': {'model': {'type': 'string'}}})
    with pytest.raises(LiteLLMIntegrationDisabled):
        validate_agent_llms(LLM(model='openhands/retired'))
    with pytest.raises(LiteLLMIntegrationDisabled):
        validate_agent_llm_payload(
            {'plugin': {'planning': {'llm': {'model': 'openhands/retired'}}}}
        )


def test_random_docker_port_preserves_requested_host() -> None:
    host_config = HostConfig(
        version='1.45', port_bindings={'8000/tcp': ('127.0.0.1', 0)}
    )
    assert host_config['PortBindings'] == {
        '8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '0'}]
    }


def test_launch_keeps_secrets_for_python_round_trip_and_encrypted_json() -> None:
    launch = DockerLaunchSpec(
        id='python',
        image='agent-server:test',
        command=['--port', '8000'],
        working_dir='/workspace/project',
        initial_env={'OH_SECRET_KEY': SecretStr('private-workspace-key')},
        docker=DockerLaunchOptions(),
    )
    python_copy = DockerLaunchSpec.model_validate(launch.model_dump())
    assert (
        python_copy.initial_env['OH_SECRET_KEY'].get_secret_value()
        == 'private-workspace-key'
    )
    assert 'private-workspace-key' not in launch.model_dump_json()
    encrypted_payload = launch.model_dump_json(context={'expose_secrets': True})
    recovered = DockerLaunchSpec.model_validate_json(encrypted_payload)
    assert (
        recovered.initial_env['OH_SECRET_KEY'].get_secret_value()
        == 'private-workspace-key'
    )
