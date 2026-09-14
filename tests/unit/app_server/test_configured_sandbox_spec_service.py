"""Stable catalog identities are separated from private provider launch settings."""

import asyncio
import logging
from collections.abc import Iterator
from typing import TypedDict
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from pydantic import JsonValue, SecretStr

from openhands.app_server.sandbox.configured_sandbox_spec_service import (
    ConfiguredSandboxSpecService,
    ConfiguredSandboxSpecServiceInjector,
)
from openhands.app_server.sandbox.sandbox_provider_config import (
    RuntimeAPITemplate,
    SandboxProviderConfig,
)
from openhands.app_server.sandbox.sandbox_spec_models import (
    SandboxSpecInfo,
    SandboxSpecInfoPage,
)
from openhands.app_server.sandbox.sandbox_spec_service import (
    get_agent_server_image,
    resolve_sandbox_spec,
)
from openhands.app_server.services.injector import InjectorState
from tests.unit.app_server.fixture_assertions import present


class NativeConfigs(TypedDict):
    configs: list[dict[str, JsonValue]]


RuntimeFixture = tuple[ConfiguredSandboxSpecService, AsyncMock]


@pytest.fixture
def native_configs() -> NativeConfigs:
    return {
        'configs': [
            {
                'name': 'native-python',
                'image': 'registry/image:same',
                'command': ['server', '--secret', 'command-secret'],
                'environment': {
                    'PRIVATE': 'env-secret',
                    'OH_SECRET_KEY': 'native-secret',
                },
                'working_dir': '/workspace/python',
                'run_as_user': 0,
                'run_as_group': 0,
                'fs_group': 0,
            },
            {
                'name': 'native-other',
                'image': 'registry/image:same',
                'command': ['server', '--option', 'other'],
                'environment': {'OTHER': 'different'},
                'working_dir': '/workspace/other',
                'run_as_user': 42,
                'run_as_group': 43,
                'fs_group': 44,
            },
        ]
    }


@pytest.fixture
def runtime_service(native_configs: NativeConfigs) -> Iterator[RuntimeFixture]:
    config = SandboxProviderConfig.from_env(
        {
            'SANDBOX_PROVIDER': 'runtime_api',
            'SANDBOX_DEFAULT_TEMPLATE': 'other',
            'SANDBOX_TEMPLATES': '[{"id":"python","config_name":"native-python"},{"id":"other","config_name":"native-other"}]',
        }
    )
    assert config is not None
    service = ConfiguredSandboxSpecService(
        config, 'https://runtime.example/', SecretStr('api-secret')
    )
    response = httpx.Response(
        200,
        json=native_configs,
        request=httpx.Request(
            'GET', 'https://runtime.example/api/warm-runtime-configs'
        ),
    )
    client = AsyncMock()
    client.get.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    with patch(
        'openhands.app_server.sandbox.configured_sandbox_spec_service.httpx.AsyncClient',
        return_value=context,
    ):
        yield service, client


async def test_two_templates_sharing_image_retain_stable_ids_and_full_settings(
    runtime_service: RuntimeFixture,
) -> None:
    service, client = runtime_service
    page = await service.search_sandbox_specs()
    assert [item.id for item in page.items] == ['python', 'other']
    first, second = await asyncio.gather(
        service.get_launch_spec('python'), service.get_launch_spec('other')
    )
    assert first.image == second.image == 'registry/image:same'
    assert first.id == 'python' and second.id == 'other'
    assert first.command == ['server', '--secret', 'command-secret']
    assert first.initial_env['PRIVATE'].get_secret_value() == 'env-secret'
    assert first.working_dir == '/workspace/python'
    assert first.provider == second.provider == 'runtime_api'
    assert (first.run_as_user, first.run_as_group, first.fs_group) == (0, 0, 0)
    assert (second.run_as_user, second.run_as_group, second.fs_group) == (42, 43, 44)
    assert second.working_dir == '/workspace/other'
    assert client.get.await_count == 1
    assert (await service.get_default_sandbox_spec()).id == 'other'


async def test_public_get_search_and_batch_never_include_private_config(
    runtime_service: RuntimeFixture,
) -> None:
    service, _ = runtime_service
    objects: list[SandboxSpecInfo | SandboxSpecInfoPage | None] = [
        await service.get_sandbox_spec('python'),
        await service.search_sandbox_specs(),
    ]
    objects.extend(
        await service.batch_get_sandbox_specs(
            ['python', 'registry/image:same', 'other']
        )
    )
    for item in objects:
        serialized = present(item).model_dump_json()
        assert not any(
            secret in serialized
            for secret in ['env-secret', 'native-secret', 'command-secret']
        )
    for item in (await service.search_sandbox_specs()).items:
        assert item.initial_env == {}
        assert item.command is None
    launch = await service.get_launch_spec('python')
    assert 'env-secret' not in repr(launch)
    assert 'command-secret' not in repr(launch)
    assert 'api-secret' not in repr(service)


async def test_native_config_legacy_image_and_bundled_default_resolve(
    runtime_service: RuntimeFixture,
) -> None:
    service, _ = runtime_service
    legacy = await service.get_launch_spec('registry/image:same')
    assert legacy.id == legacy.image == 'registry/image:same'
    assert legacy.working_dir == '/workspace/python'
    default = await service.get_sandbox_spec(get_agent_server_image())
    assert default is not None
    assert default.id == get_agent_server_image()
    assert default.initial_env == {}
    assert await service.get_sandbox_spec('unregistered') is None
    batch = await service.batch_get_sandbox_specs(
        ['other', 'missing', 'python', 'other']
    )
    assert [item.id if item else None for item in batch] == [
        'other',
        None,
        'python',
        'other',
    ]


async def test_default_and_pagination(runtime_service: RuntimeFixture) -> None:
    service, _ = runtime_service
    first = await service.search_sandbox_specs(limit=1)
    assert first.next_page_id == '1'
    second = await service.search_sandbox_specs(page_id=first.next_page_id, limit=1)
    assert second.items[0].id == 'other'
    assert second.next_page_id is None
    with pytest.raises(ValueError):
        await service.search_sandbox_specs(page_id='-1')


async def test_missing_explicit_native_config_is_clear_and_never_falls_back(
    runtime_service: RuntimeFixture,
) -> None:
    service, client = runtime_service
    client.get.return_value = httpx.Response(
        200,
        json={'configs': []},
        request=httpx.Request('GET', 'https://runtime.example'),
    )
    with pytest.raises(ValueError, match="template 'python'.*not found"):
        await service.get_sandbox_spec('python')


@pytest.mark.parametrize(
    'response',
    [
        {
            'configs': [
                {
                    'name': 'native-python',
                    'image': 'image',
                    'command': ['private-secret'],
                    'environment': {'PRIVATE': 'private-secret'},
                }
            ]
        },
        {
            'configs': [
                {
                    'name': 'native-python',
                    'image': 'image',
                    'command': ['private-secret'],
                    'environment': {'PRIVATE': {'invalid': 'private-secret'}},
                    'working_dir': '/workspace',
                }
            ]
        },
        {'configs': 'private-secret'},
        None,
    ],
)
async def test_invalid_native_response_never_prints_input(
    runtime_service: RuntimeFixture, response: JsonValue
) -> None:
    service, client = runtime_service
    client.get.return_value = httpx.Response(
        200, json=response, request=httpx.Request('GET', 'https://runtime.example')
    )
    with pytest.raises(ValueError, match='invalid template configurations') as error:
        await service.get_launch_spec('python')
    assert 'private-secret' not in str(error.value)
    assert error.value.__suppress_context__ is True


async def test_provider_error_has_safe_message(runtime_service: RuntimeFixture) -> None:
    service, client = runtime_service
    client.get.side_effect = httpx.ConnectError('private-secret')
    with pytest.raises(ValueError, match='Unable to fetch') as error:
        await service.get_launch_spec('python')
    assert 'private-secret' not in str(error.value)


async def test_cache_survives_injection_and_expires(
    runtime_service: RuntimeFixture,
) -> None:
    service, client = runtime_service
    injector = ConfiguredSandboxSpecServiceInjector(
        provider_config=service.provider_config,
        api_url=service.api_url,
        api_key=service.api_key,
    )
    async with injector.context(InjectorState()) as first:
        await asyncio.gather(*(first.get_sandbox_spec('python') for _ in range(3)))
    async with injector.context(InjectorState()) as second:
        await second.get_sandbox_spec('other')
    assert first is second
    assert client.get.await_count == 1
    assert isinstance(first, ConfiguredSandboxSpecService)
    first._cache_expires_at = 0
    await first.get_sandbox_spec('python')
    assert client.get.await_count == 2


async def test_mutating_launch_does_not_mutate_cached_native_config(
    runtime_service: RuntimeFixture,
) -> None:
    service, _ = runtime_service
    launch = await service.get_launch_spec('python')
    launch.initial_env['PRIVATE'] = SecretStr('changed')
    present(launch.command).append('changed')
    original = await service.get_launch_spec('python')
    assert original.initial_env['PRIVATE'].get_secret_value() == 'env-secret'
    assert 'changed' not in present(original.command)


async def test_native_null_security_fields_keep_defaults(
    runtime_service: RuntimeFixture, native_configs: NativeConfigs
) -> None:
    service, client = runtime_service
    for name in ('run_as_user', 'run_as_group', 'fs_group'):
        native_configs['configs'][0][name] = None
    client.get.return_value = httpx.Response(
        200,
        json=native_configs,
        request=httpx.Request('GET', 'https://runtime.example'),
    )
    launch = await service.get_launch_spec('python')
    assert launch.provider == 'runtime_api'
    assert (launch.run_as_user, launch.run_as_group, launch.fs_group) == (
        10001,
        10001,
        10001,
    )


async def test_stale_user_default_falls_back_but_explicit_missing_id_errors(
    runtime_service: RuntimeFixture,
) -> None:
    service, _ = runtime_service
    assert (
        await resolve_sandbox_spec(
            None, 'removed', service, logging.getLogger(__name__)
        )
    ).id == 'other'
    assert (
        await resolve_sandbox_spec(
            'python', 'other', service, logging.getLogger(__name__)
        )
    ).id == 'python'
    with pytest.raises(ValueError, match='not found'):
        await resolve_sandbox_spec(
            'removed', 'other', service, logging.getLogger(__name__)
        )


async def test_runtime_optional_init_key_reference_does_not_replace_native_startup(
    runtime_service: RuntimeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = runtime_service
    service.provider_config.templates[0] = RuntimeAPITemplate(
        id='python', config_name='native-python', init_api_key_env='NATIVE_INIT_KEY'
    )
    monkeypatch.setenv('NATIVE_INIT_KEY', 'referenced-native-key')
    launch = await service.get_launch_spec('python')
    assert launch.provider == 'runtime_api'
    assert present(launch.init_api_key).get_secret_value() == 'referenced-native-key'
    assert launch.initial_env['OH_SECRET_KEY'].get_secret_value() == 'native-secret'
    assert 'OH_SESSION_API_KEYS_0' not in launch.initial_env
    assert 'referenced-native-key' not in repr(launch)
    assert 'referenced-native-key' not in launch.model_dump_json()
    assert (
        'referenced-native-key'
        not in present(await service.get_sandbox_spec('python')).model_dump_json()
    )


async def test_direct_sandbox_router_keeps_template_id() -> None:
    from openhands.app_server.sandbox.sandbox_router import start_sandbox

    service = Mock()
    service.start_sandbox = AsyncMock(return_value=Mock())
    await start_sandbox(sandbox_spec_id='python', sandbox_service=service)
    service.start_sandbox.assert_awaited_once_with('python')


async def test_legacy_bundled_image_uses_native_metadata_when_registered(
    runtime_service: RuntimeFixture, native_configs: NativeConfigs
) -> None:
    service, client = runtime_service
    native_configs['configs'][0]['image'] = get_agent_server_image()
    client.get.return_value = httpx.Response(
        200,
        json=native_configs,
        request=httpx.Request('GET', 'https://runtime.example'),
    )
    legacy = await service.get_launch_spec(get_agent_server_image())
    assert legacy.id == get_agent_server_image()
    assert legacy.working_dir == '/workspace/python'
    assert legacy.provider == 'runtime_api'
    assert legacy.run_as_user == 0
