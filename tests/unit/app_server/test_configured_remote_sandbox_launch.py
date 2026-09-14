"""Catalog-backed remote launches keep native configuration and stable inventory IDs."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr, TypeAdapter

from openhands.app_server.sandbox.configured_sandbox_spec_service import (
    ConfiguredSandboxSpecService,
)
from openhands.app_server.sandbox.remote_sandbox_service import (
    StoredRemoteSandbox,
)
from openhands.app_server.sandbox.runtime_api_models import RuntimeStartRequest
from openhands.app_server.sandbox.sandbox_models import SandboxInfo, SandboxStatus
from openhands.app_server.sandbox.sandbox_provider_config import (
    RuntimeAPILaunchSpec,
    SandboxProviderConfig,
)
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.settings.settings_models import SandboxGroupingStrategy
from openhands.app_server.user.user_models import UserInfo
from tests.unit.app_server.fixture_assertions import present
from tests.unit.app_server.service_mock_fixtures import MockedRemoteService


@pytest.fixture
def service() -> Iterator[MockedRemoteService]:
    catalog = ConfiguredSandboxSpecService(
        present(
            SandboxProviderConfig.from_env(
                {
                    'SANDBOX_PROVIDER': 'runtime_api',
                    'SANDBOX_DEFAULT_TEMPLATE': 'python',
                    'SANDBOX_TEMPLATES': '[{"id":"python","config_name":"native-python"}]',
                }
            )
        )
    )
    launch = RuntimeAPILaunchSpec(
        id='python',
        image='registry/actual-image:tag',
        command=['server', '--port', '60000', '--custom', 'private-command-value'],
        initial_env={
            'PRESET_TOKEN': SecretStr('private-env-value'),
            'OH_SECRET_KEY': SecretStr('native-bootstrap'),
        },
        working_dir='/workspace/native',
        run_as_user=0,
        run_as_group=42,
        fs_group=0,
    )
    user = AsyncMock()
    user.get_user_id.return_value = 'owner'
    user.get_default_sandbox_spec_id.return_value = 'python'
    user.get_user_info.return_value = UserInfo(
        id='owner', sandbox_grouping_strategy=SandboxGroupingStrategy.NO_GROUPING
    )
    instance = MockedRemoteService(
        sandbox_spec_service=catalog,
        api_url='https://runtime.example',
        api_key='api-key',
        web_url='https://app.example',
        resource_factor=2,
        runtime_class=None,
        start_sandbox_timeout=30,
        max_num_sandboxes=5,
        user_context=user,
        httpx_client=AsyncMock(),
        db_session=MagicMock(),
    )
    with (
        patch.object(
            catalog, 'get_launch_spec', new_callable=AsyncMock, return_value=launch
        ),
        patch.object(instance, 'pause_old_sandboxes', new_callable=AsyncMock),
    ):
        yield instance


async def test_native_start_payload_uses_image_and_complete_config(
    service: MockedRemoteService,
) -> None:
    spec = await service.sandbox_spec_service.get_sandbox_spec('python')
    assert spec is not None
    assert spec.initial_env == {} and spec.command is None
    environment = await service._init_environment(spec, 'sandbox-id')
    payload = await service.build_sandbox_start_request(spec, 'sandbox-id', environment)
    assert payload['image'] == 'registry/actual-image:tag'
    assert payload['command'] == [
        'server',
        '--port',
        '60000',
        '--custom',
        'private-command-value',
    ]
    assert payload['environment']['PRESET_TOKEN'] == 'private-env-value'
    assert payload['environment']['OH_SECRET_KEY'] == 'native-bootstrap'
    assert payload['working_dir'] == '/workspace/native'
    assert (payload['run_as_user'], payload['run_as_group'], payload['fs_group']) == (
        0,
        42,
        0,
    )
    assert payload['session_id'] == 'sandbox-id'
    assert payload['resource_factor'] == 2
    assert 'config_name' not in payload


async def test_inventory_persists_catalog_id_and_pinned_workdir(
    service: MockedRemoteService,
) -> None:
    response = httpx.Response(
        200,
        json={'session_api_key': 'native-active-key'},
        request=httpx.Request('POST', 'https://runtime.example/start'),
    )
    stored_rows: list[StoredRemoteSandbox] = []
    service.db_session.add.side_effect = stored_rows.append
    sandbox = SandboxInfo(
        id='sandbox-id',
        created_by_user_id='owner',
        sandbox_spec_id='python',
        session_api_key='native-active-key',
        status=SandboxStatus.RUNNING,
    )
    with (
        patch.object(
            service,
            '_send_runtime_api_request',
            new_callable=AsyncMock,
            return_value=response,
        ) as send,
        patch.object(
            service, 'get_sandbox', new_callable=AsyncMock, return_value=sandbox
        ),
    ):
        await service.start_sandbox('python', 'sandbox-id')
    (stored,) = stored_rows
    assert stored.id == 'sandbox-id'
    assert stored.sandbox_spec_id == 'python'
    assert stored.working_dir == '/workspace/native'
    assert stored.session_api_key_hash != 'native-active-key'
    payload = TypeAdapter(RuntimeStartRequest).validate_python(
        send.call_args.kwargs['json']
    )
    assert payload['image'] == 'registry/actual-image:tag'
    service.httpx_client.post.assert_not_called()


@pytest.mark.parametrize(
    'catalog_result',
    [
        None,
        SandboxSpecInfo(
            id='python', command=None, working_dir='/new-template-location'
        ),
    ],
)
async def test_archive_keeps_workdir_after_template_edit_or_removal(
    service: MockedRemoteService, catalog_result: SandboxSpecInfo | None
) -> None:
    with patch.object(
        service.sandbox_spec_service,
        'get_sandbox_spec',
        new_callable=AsyncMock,
        return_value=catalog_result,
    ) as lookup:
        stored = StoredRemoteSandbox(
            id='sandbox-id',
            created_by_user_id='owner',
            sandbox_spec_id='python',
            working_dir='/original/workspace',
        )
        assert (
            await service._resolve_archive_path(stored, 'conversation-id', None)
            == '/original/workspace'
        )
        lookup.assert_not_awaited()


async def test_legacy_record_without_snapshot_uses_spec_fallback(
    service: MockedRemoteService,
) -> None:
    stored = StoredRemoteSandbox(
        id='sandbox-id', created_by_user_id='owner', sandbox_spec_id='python'
    )
    assert stored.working_dir is None
    assert (
        await service._resolve_archive_path(stored, 'conversation-id', None)
        == '/workspace/native'
    )


async def test_per_conversation_archive_path_still_has_priority(
    service: MockedRemoteService,
) -> None:
    stored = StoredRemoteSandbox(
        id='sandbox-id',
        created_by_user_id='owner',
        sandbox_spec_id='python',
        working_dir='/original/workspace',
    )
    assert (
        await service._resolve_archive_path(
            stored, 'conversation-id', '/pinned/conversation'
        )
        == '/pinned/conversation'
    )
