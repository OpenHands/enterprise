"""Managed lifecycle tests use PostgreSQL inventory and mocked Docker/HTTP only."""

import json
import secrets
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import ParamSpec, TypeVar
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from docker import DockerClient
from docker.models.volumes import Volume
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
    AppConversationInfoPage,
)
from openhands.app_server.errors import (
    PermissionsError,
    SandboxDeleteRetryError,
    SandboxError,
)
from openhands.app_server.sandbox.configured_sandbox_spec_service import (
    ConfiguredSandboxSpecService,
)
from openhands.app_server.sandbox.managed_docker_sandbox_service import (
    MANAGED_LABEL,
    RESOURCE_LABEL,
    ManagedDockerSandboxService,
)
from openhands.app_server.sandbox.sandbox_models import SandboxStatus
from openhands.app_server.sandbox.sandbox_provider_config import SandboxProviderConfig
from openhands.app_server.sandbox.sandbox_service import SandboxService
from openhands.app_server.sandbox.sql_sandbox_store import (
    SQLSandboxStore,
    StoredManagedSandbox,
)
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.user.specifiy_user_context import ADMIN, SpecifyUserContext
from openhands.app_server.utils.encryption_key import EncryptionKey
from openhands.app_server.utils.litellm_integration import LiteLLMIntegrationDisabled
from tests.postgres_testdb import TestDatabase
from tests.unit.app_server.fixture_assertions import present
from tests.unit.app_server.managed_docker_fixtures import NativeDocker

ManagedFixture = tuple[ManagedDockerSandboxService, NativeDocker]
P = ParamSpec('P')
T = TypeVar('T')

_VALIDATE_RESUME = SandboxService.validate_resume_configuration


@pytest.fixture(autouse=True)
def mock_resume_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SandboxService, 'validate_resume_configuration', AsyncMock())


@pytest.fixture
async def managed(async_engine: AsyncEngine) -> AsyncIterator[ManagedFixture]:
    config = SandboxProviderConfig.from_env(
        {
            'SANDBOX_PROVIDER': 'docker',
            'SANDBOX_DEFAULT_TEMPLATE': 'python',
            'SANDBOX_TEMPLATES': json.dumps(
                [
                    {
                        'id': 'python',
                        'image': 'agent-server:1.47.0-python',
                        'initial_env': {'TOKEN_FOR_TOOL': 'sensitive-tool-value'},
                        'docker': {
                            'webhook_url': 'http://host.docker.internal:3000/api/v1/webhooks',
                            'ports': {'AGENT_SERVER': 8000, 'WORKER_1': 8011},
                            'mounts': [
                                {
                                    'source': '/operator/data',
                                    'target': '/reference',
                                    'read_only': True,
                                }
                            ],
                        },
                    }
                ]
            ),
        }
    )
    assert config is not None
    jwt = JwtService(
        [EncryptionKey(id='test', key=SecretStr('stable-test-encryption-master-key'))]
    )
    store = SQLSandboxStore(async_engine, jwt, 'alice')
    native = NativeDocker()
    with native.intercept():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(native.http)
        ) as http:
            service = ManagedDockerSandboxService(
                ConfiguredSandboxSpecService(config),
                store,
                SpecifyUserContext('alice'),
                http,
                'python',
                docker_client=native.client,
                start_sandbox_timeout=0.2,
                poll_interval=0,
                web_url='http://frontend.local',
                permitted_cors_origins=['http://other.local'],
            )
            yield service, native


@pytest.mark.asyncio
async def test_start_persists_encrypted_distinct_credentials_and_launch(
    managed: ManagedFixture, async_engine: AsyncEngine
) -> None:
    service, native = managed
    info = await service.start_sandbox(sandbox_id='my-sandbox')
    assert info.session_api_key is not None
    assert info.exposed_urls is not None
    assert info.status == SandboxStatus.RUNNING
    assert info.sandbox_spec_id == 'python'
    assert info.working_dir == '/workspace/project'
    row = present(await service.store.get(info.id))
    credentials = service.store.credentials(row)
    _, init = native.init_posts[0]
    assert info.session_api_key == init['session_api_keys'][0]
    assert init['secret_key'] == credentials.workspace_key.get_secret_value()
    assert info.session_api_key != init['secret_key']
    assert native.init_posts[0][0]['x-init-api-key'] == init['secret_key']
    assert init['conversation_worktree_root'] == '/workspace/worktrees'
    assert init['webhooks'] == [
        {'base_url': 'http://host.docker.internal:3000/api/v1/webhooks'}
    ]
    assert init['allow_cors_origins'] == ['http://frontend.local', 'http://other.local']
    launch_call = native.created[-1]
    assert launch_call['image'] == 'agent-server:1.47.0-python'
    assert launch_call['command'] == ['--port', '8000']
    assert launch_call['ports'] == {
        '8000/tcp': ('127.0.0.1', 0),
        '8011/tcp': ('127.0.0.1', 0),
    }
    assert launch_call['environment']['OH_SECRET_KEY'] == init['secret_key']
    service_token = launch_call['environment']['OH_SESSION_API_KEYS_0']
    assert (
        len(
            {
                service_token,
                info.session_api_key,
                init['secret_key'],
            }
        )
        == 3
    )
    assert (
        launch_call['environment']['OH_ALLOW_CORS_ORIGINS_0'] == 'http://frontend.local'
    )
    assert launch_call['environment']['OH_ALLOW_CORS_ORIGINS_1'] == 'http://other.local'
    assert launch_call['environment']['OH_DEFERRED_INIT'] == 'true'
    assert info.session_api_key not in str(launch_call)
    assert init['secret_key'] not in str(launch_call['labels'])
    assert 'static-bootstrap' not in str(launch_call['labels'])
    assert [url.name for url in info.exposed_urls] == ['AGENT_SERVER', 'WORKER_1']
    assert info.exposed_urls[0].url == 'http://localhost:41000'
    async with async_engine.connect() as connection:
        raw = (
            await connection.execute(
                text(
                    'SELECT launch_spec_ciphertext, session_key_ciphertext, workspace_key_ciphertext FROM v1_managed_sandbox'
                )
            )
        ).one()
    for secret in (
        'static-bootstrap',
        'sensitive-tool-value',
        info.session_api_key,
        init['secret_key'],
    ):
        assert all(secret not in value for value in raw)
        assert secret not in repr(row)
        assert secret not in repr(credentials)
    # A distinct JwtService + SQL store reconstruct the private launch correctly.
    recovered = SQLSandboxStore(
        async_engine,
        JwtService(
            [
                EncryptionKey(
                    id='test', key=SecretStr('stable-test-encryption-master-key')
                )
            ]
        ),
        'alice',
    )
    launch, cors = recovered.launch(present(await recovered.get(info.id)))
    assert launch.initial_env['OH_SECRET_KEY'] == credentials.workspace_key
    assert (
        launch.initial_env['TOKEN_FOR_TOOL'].get_secret_value()
        == 'sensitive-tool-value'
    )
    assert launch.working_dir == '/workspace/project'
    assert cors == ['http://frontend.local', 'http://other.local']


@pytest.mark.asyncio
async def test_owner_checks_precede_native_lookup_for_every_operation(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    info = await service.start_sandbox(sandbox_id='alice-id')
    assert info.session_api_key is not None
    assert info.exposed_urls is not None
    other = replace(
        service,
        store=replace(service.store, owner_id='bob'),
        user_context=SpecifyUserContext('bob'),
    )
    before = len(native.calls)
    assert await other.get_sandbox(info.id) is None
    assert await other.batch_get_sandboxes([info.id, info.id]) == [None, None]
    assert (await other.search_sandboxes()).items == []
    assert (
        await other.get_sandbox_record_by_session_api_key(info.session_api_key) is None
    )
    assert await other.get_sandbox_by_session_api_key(info.session_api_key) is None
    assert not await other.pause_sandbox(info.id)
    assert not await other.resume_sandbox(info.id)
    assert not await other.delete_sandbox(info.id)
    with pytest.raises(SandboxError, match='reserved'):
        await other.start_sandbox(sandbox_id=info.id)
    assert len(native.calls) == before
    missing_user = replace(
        service,
        store=replace(service.store, owner_id=None),
        user_context=SpecifyUserContext(None),
    )
    assert await missing_user.get_sandbox(info.id) is None
    with pytest.raises(PermissionsError):
        await missing_user.start_sandbox()
    admin = replace(
        service,
        store=replace(service.store, owner_id=None, is_admin=True),
        user_context=ADMIN,
    )
    # Authentication is DB-only, even if the Docker daemon is unreachable.
    native.fail_lookup = True
    admin.docker_client = None
    with patch(
        'openhands.app_server.sandbox.managed_docker_sandbox_service.docker.from_env',
        side_effect=AssertionError('must not connect'),
    ):
        assert (
            present(
                await admin.get_sandbox_record_by_session_api_key(info.session_api_key)
            )
        ).created_by_user_id == 'alice'
    assert await admin.get_sandbox_record_by_session_api_key('bad-key') is None


@pytest.mark.asyncio
async def test_pause_unpause_preserves_process_and_restart_rotates_auth_only(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    row = present(await service.store.get(first.id))
    native_id = row.container_id
    workspace_key = service.store.credentials(row).workspace_key
    generation = row.initialized_generation
    container = native.get(native_id)
    assert await service.pause_sandbox(first.id)
    assert (present(await service.get_sandbox(first.id))).status == SandboxStatus.PAUSED
    assert (
        await service.get_sandbox_record_by_session_api_key(first.session_api_key)
        is None
    )
    # Recreate the app service and remove the template; persisted launch is enough.
    service = replace(
        service, store=replace(service.store), sandbox_spec_service=MagicMock()
    )
    assert await service.resume_sandbox(first.id)
    assert len(native.init_posts) == 1
    second = present(await service.get_sandbox(first.id))
    assert second.session_api_key == first.session_api_key
    assert (
        present(await service.store.get(first.id))
    ).initialized_generation == generation
    container.attrs['State']['Status'] = 'exited'
    assert (present(await service.get_sandbox(first.id))).status == SandboxStatus.PAUSED
    assert await service.resume_sandbox(first.id)
    third = present(await service.get_sandbox(first.id))
    assert third.session_api_key != first.session_api_key
    row = present(await service.store.get(first.id))
    assert row.container_id == native_id
    assert service.store.credentials(row).workspace_key == workspace_key
    assert row.initialized_generation != generation
    assert len(native.init_posts) == 2
    assert (
        await service.get_sandbox_record_by_session_api_key(first.session_api_key)
        is None
    )
    # Repeated tabs' resume is idempotent once ready.
    assert await service.resume_sandbox(first.id)
    assert len(native.init_posts) == 2


@pytest.mark.asyncio
async def test_unexpected_restart_reads_never_initialize_or_publish_keys(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    row = present(await service.store.get(first.id))
    native.get(row.container_id).start()
    posts = len(native.init_posts)
    info = present(await service.get_sandbox(first.id))
    assert info.status == SandboxStatus.STARTING
    assert info.session_api_key is None and info.exposed_urls is None
    assert len(native.init_posts) == posts
    assert await service.resume_sandbox(first.id)
    assert len(native.init_posts) == posts + 1


@pytest.mark.asyncio
async def test_status_unknown_vs_confirmed_missing_and_batch_deduplication(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    info = await service.start_sandbox()
    assert info.session_api_key is not None
    assert info.exposed_urls is not None
    row = present(await service.store.get(info.id))
    container = native.get(row.container_id)
    native.fail_lookup = True
    unknown = present(await service.get_sandbox(info.id))
    assert unknown.status == SandboxStatus.UNKNOWN
    assert unknown.session_api_key is None and unknown.exposed_urls is None
    batch = await service.batch_get_sandboxes([info.id, 'not-owned', info.id])
    assert [item.status if item else None for item in batch] == [
        SandboxStatus.UNKNOWN,
        None,
        SandboxStatus.UNKNOWN,
    ]
    native.fail_lookup = False
    container.reload.reset_mock()
    batch = await service.batch_get_sandboxes([info.id, 'not-owned', info.id])
    assert batch[0] is batch[2]
    container.reload.assert_called_once()
    assert native.listed[-1]['all'] and native.listed[-1]['sparse']
    assert native.listed[-1]['filters']['label'] == f'{MANAGED_LABEL}=true'
    assert len(native.listed[-1]['filters']['id']) == 1
    native.items.pop(row.container_name)
    assert (present(await service.get_sandbox(info.id))).status == SandboxStatus.MISSING
    assert await service.store.get(info.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['fail_create', 'fail_init', 'fail_ready'])
async def test_failed_start_cleans_only_owned_attempt_resources(
    managed: ManagedFixture, failure: str
) -> None:
    service, native = managed
    setattr(native, failure, True)
    with pytest.raises(SandboxError):
        await service.start_sandbox()
    assert native.items == {}
    assert native.volume_items == {}
    assert await service.store.page(0, 100) == []
    assert all(
        '/operator/data' not in identity
        for operation, identity in native.calls
        if operation == 'get'
    )


@pytest.mark.asyncio
async def test_partial_cleanup_retains_record_and_retries_without_container(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    row = present(await service.store.get(first.id))
    native.items.pop(row.container_name)
    native.fail_cleanup = True
    with pytest.raises(SandboxDeleteRetryError):
        await service.delete_sandbox(first.id)
    assert (present(await service.store.get(first.id))).desired_state == 'deleting'
    assert (
        await service.get_sandbox_record_by_session_api_key(first.session_api_key)
        is None
    )
    assert len(native.volume_items) == 1
    native.fail_cleanup = False
    assert await service.delete_sandbox(first.id)
    assert native.volume_items == {}
    assert await service.store.get(first.id) is None


@pytest.mark.asyncio
async def test_failed_allocation_cleanup_retains_recoverable_names(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    native.fail_init = True
    native.fail_cleanup = True
    with pytest.raises(SandboxError):
        await service.start_sandbox(sandbox_id='retry-cleanup')
    row = present(await service.store.get('retry-cleanup'))
    assert row.desired_state == 'cleanup'
    assert row.session_api_key_hash is None
    assert row.owned_volume_names
    native.fail_cleanup = False
    assert await service.delete_sandbox(row.id)
    assert native.items == {} and native.volume_items == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('foreign', ['container', 'volume'])
async def test_foreign_labels_prevent_resource_deletion(
    managed: ManagedFixture, foreign: str
) -> None:
    service, native = managed
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    row = present(await service.store.get(first.id))
    resource = (
        native.get(row.container_id)
        if foreign == 'container'
        else native.get_volume(row.owned_volume_names[0])
    )
    labels = (
        native.get(row.container_id).attrs['Config']['Labels']
        if foreign == 'container'
        else native.get_volume(row.owned_volume_names[0]).attrs['Labels']
    )
    labels[RESOURCE_LABEL] = 'foreign-instance'
    with pytest.raises(SandboxDeleteRetryError):
        await service.delete_sandbox(first.id)
    resource.remove.assert_not_called()
    assert await service.store.get(first.id) is not None


@pytest.mark.asyncio
async def test_cap_one_pauses_old_owned_sandbox_and_stops_on_failure(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    service.max_num_sandboxes = 1
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    second = await service.start_sandbox()
    assert second.session_api_key is not None
    assert second.exposed_urls is not None
    assert (present(await service.get_sandbox(first.id))).status == SandboxStatus.PAUSED
    assert (
        present(await service.get_sandbox(second.id))
    ).status == SandboxStatus.RUNNING
    native.fail_pause = True
    before = len(native.created)
    with pytest.raises(SandboxError):
        await service.start_sandbox()
    assert len(native.created) == before
    assert (
        present(await service.get_sandbox(second.id))
    ).status == SandboxStatus.STARTING
    assert (
        await service.get_sandbox_record_by_session_api_key(second.session_api_key)
        is None
    )


@pytest.mark.asyncio
async def test_invalid_template_has_no_inventory_or_admission_side_effects(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    with pytest.raises(SandboxError):
        await service.start_sandbox('not-configured')
    assert native.calls == []
    assert await service.store.page(0, 100) == []


@pytest.mark.asyncio
async def test_native_client_creation_is_lazy_and_off_event_loop(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    event_loop_thread = threading.get_ident()
    service.docker_client = None

    def make_client(*, timeout: int) -> DockerClient:
        assert threading.get_ident() != event_loop_thread
        assert timeout == 20
        return native.client

    with patch(
        'openhands.app_server.sandbox.managed_docker_sandbox_service.docker.from_env',
        side_effect=make_client,
    ) as make:
        assert await service.get_sandbox('absent') is None
        make.assert_not_called()
        await service.start_sandbox()
        make.assert_called_once()


@pytest.mark.asyncio
async def test_required_archive_blocks_on_failure_and_uses_pinned_path(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    prefix = (
        'openhands.app_server.sandbox.managed_docker_sandbox_service.workspace_archive'
    )
    with (
        patch(f'{prefix}.archive_enabled', return_value=True),
        patch(f'{prefix}.archive_required', return_value=True),
        patch(
            f'{prefix}.archive_workspace', new_callable=AsyncMock, return_value=False
        ) as archive,
    ):
        assert not await service.archive_conversation_workspace(
            first.id, 'conversation', '/workspace/project/conversation'
        )
        assert (
            archive.call_args.kwargs['archive_path']
            == '/workspace/project/conversation'
        )
        native.fail_lookup = True
        assert not await service.archive_conversation_workspace(
            first.id, 'conversation', '/workspace/project/conversation'
        )
        archive.assert_awaited_once()
        native.fail_lookup = False
        row = present(await service.store.get(first.id))
        native.items.pop(row.container_name)
        assert not await service.archive_conversation_workspace(
            first.id, 'conversation', '/workspace/project/conversation'
        )


@pytest.mark.asyncio
async def test_database_operation_lock_rejects_duplicate_and_releases(
    managed: ManagedFixture,
) -> None:
    service, _ = managed
    async with service.store.lock('sandbox:test'):
        with pytest.raises(SandboxError) as busy:
            async with replace(service.store).lock('sandbox:test'):
                pytest.fail('same sandbox must not have two operations')
        assert busy.value.status_code == 503
    async with service.store.lock('sandbox:test'):
        pass


@pytest.mark.asyncio
async def test_durable_row_survives_unrelated_request_rollback(
    managed: ManagedFixture, async_session_maker: async_sessionmaker[AsyncSession]
) -> None:
    service, _ = managed
    async with async_session_maker() as request_session:
        await request_session.execute(text('SELECT 1'))
        first = await service.start_sandbox()
        assert first.session_api_key is not None
        assert first.exposed_urls is not None
        await request_session.rollback()
    assert await service.store.get(first.id) is not None


@pytest.mark.asyncio
async def test_stale_user_default_falls_back_and_duplicate_start_is_idempotent(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    service.user_context = MagicMock()
    service.user_context.get_user_id = AsyncMock(return_value='alice')
    service.user_context.get_default_sandbox_spec_id = AsyncMock(
        return_value='removed-template'
    )
    first = await service.start_sandbox(sandbox_id='duplicate')
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    created = len(native.created)
    second = await service.start_sandbox(sandbox_id='duplicate')
    assert second.session_api_key is not None
    assert second.exposed_urls is not None
    assert second.id == first.id
    assert second.session_api_key == first.session_api_key
    assert len(native.created) == created
    assert len(native.init_posts) == 1


@pytest.mark.asyncio
async def test_pause_revocation_survives_failure_of_post_pause_db_commit(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    original_save = service.store.save

    async def save(row: StoredManagedSandbox) -> None:
        if row.desired_state == 'paused':
            raise RuntimeError('database failed after Docker pause')
        await original_save(row)

    with patch.object(service.store, 'save', side_effect=save):
        with pytest.raises(RuntimeError):
            await service.pause_sandbox(first.id)
    assert (
        await service.get_sandbox_record_by_session_api_key(first.session_api_key)
        is None
    )
    row = present(await service.store.get(first.id))
    assert row.desired_state == 'starting'
    assert native.get(row.container_id).attrs['State']['Status'] == 'paused'
    assert await service.resume_sandbox(first.id)
    assert len(native.init_posts) == 1
    assert (
        present(await service.get_sandbox(first.id))
    ).session_api_key == first.session_api_key


@pytest.mark.asyncio
async def test_resume_restores_revoked_hash_for_ready_process(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    row = present(await service.store.get(first.id))
    row.session_api_key_hash = None
    await service.store.save(row)
    assert await service.resume_sandbox(first.id)
    assert (
        present(
            await service.get_sandbox_record_by_session_api_key(first.session_api_key)
        )
    ).id == first.id
    assert len(native.init_posts) == 1


@pytest.mark.asyncio
async def test_crash_after_init_before_activation_recovers_same_credentials(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    row = present(await service.store.get(first.id))
    row.initializing_generation = row.initialized_generation
    row.initialized_generation = None
    row.desired_state = 'starting'
    row.session_api_key_hash = None
    await service.store.save(row)
    service = replace(service, store=replace(service.store))
    assert await service.resume_sandbox(first.id)
    recovered = present(await service.get_sandbox(first.id))
    assert recovered.session_api_key == first.session_api_key
    assert len(native.init_posts) == 1


@pytest.mark.asyncio
async def test_cap_only_pauses_owner_inventory(managed: ManagedFixture) -> None:
    service, native = managed
    service.max_num_sandboxes = 1
    alice_first = await service.start_sandbox()
    assert alice_first.session_api_key is not None
    assert alice_first.exposed_urls is not None
    bob = replace(
        service,
        store=replace(service.store, owner_id='bob'),
        user_context=SpecifyUserContext('bob'),
    )
    bob_first = await bob.start_sandbox()
    assert bob_first.session_api_key is not None
    assert bob_first.exposed_urls is not None
    bob_row = present(await bob.store.get(bob_first.id))
    bob_container = native.get(bob_row.container_id)
    await service.start_sandbox()
    bob_container.pause.assert_not_called()
    assert (
        present(await service.get_sandbox(alice_first.id))
    ).status == SandboxStatus.PAUSED


@pytest.mark.asyncio
async def test_init_web_url_uses_sandbox_public_address_and_internal_rewrite(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    with patch(
        'openhands.app_server.sandbox.managed_docker_sandbox_service.replace_localhost_hostname_for_docker',
        side_effect=lambda url: url.replace('localhost', 'host.docker.internal'),
    ) as rewrite:
        first = await service.start_sandbox()
        assert first.session_api_key is not None
        assert first.exposed_urls is not None
    assert first.exposed_urls[0].url.startswith('http://localhost:')
    assert native.init_posts[0][1]['web_url'] == first.exposed_urls[0].url
    assert native.init_posts[0][1]['webhooks'][0]['base_url'].startswith(
        'http://host.docker.internal:3000/'
    )
    assert rewrite.call_count > 0


@pytest.mark.asyncio
async def test_public_https_routing_pins_resource_service_and_survives_resume(
    managed: ManagedFixture,
) -> None:
    from urllib.parse import parse_qs, urlsplit

    service, native = managed
    template = service.sandbox_spec_service.provider_config.templates[0]
    assert template.provider == 'docker'
    template.docker.public_url_pattern = (
        'https://{container_port}-{resource_id}.sandboxes.example.com'
    )
    template.docker.ports['VSCODE'] = 8001
    service.web_url = 'https://app.example.com'
    service.permitted_cors_origins = []
    first = await service.start_sandbox(sandbox_id='user-controlled-name')
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    row = present(await service.store.get(first.id))
    launch, _ = service.store.launch(row)
    assert row.resource_id != first.id
    assert row.container_name == f'oh-managed-{row.resource_id}'
    assert len(first.exposed_urls) == 3
    for exposed in first.exposed_urls:
        url = urlsplit(exposed.url)
        assert url.scheme == 'https'
        assert url.hostname == f'{exposed.port}-{row.resource_id}.sandboxes.example.com'
        assert url.port is None
    vscode_url = next(url.url for url in first.exposed_urls if url.name == 'VSCODE')
    assert parse_qs(urlsplit(vscode_url).query)['tkn'] == [
        launch.initial_env['OH_SESSION_API_KEYS_0'].get_secret_value()
    ]
    assert native.init_posts[0][1]['web_url'] == first.exposed_urls[0].url
    assert native.init_posts[0][1]['allow_cors_origins'] == ['https://app.example.com']
    assert native.init_posts[0][1]['webhooks'] == [
        {'base_url': 'http://host.docker.internal:3000/api/v1/webhooks'}
    ]
    assert all(
        binding[0] == '127.0.0.1' for binding in native.created[-1]['ports'].values()
    )
    # Existing inventory uses its encrypted routing snapshot after catalog changes.
    template.docker.public_url_pattern = None
    container = native.get(row.container_id)
    container.attrs['State']['Status'] = 'exited'
    assert await service.resume_sandbox(first.id)
    resumed = present(await service.get_sandbox(first.id))
    assert resumed.exposed_urls == first.exposed_urls
    assert resumed.session_api_key != first.session_api_key


@pytest.mark.asyncio
async def test_resuming_failed_health_does_not_delete_workspace(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    row = present(await service.store.get(first.id))
    container = native.get(row.container_id)
    container.attrs['State']['Status'] = 'exited'
    native.fail_init = True
    with pytest.raises(SandboxError):
        await service.resume_sandbox(first.id)
    assert native.get_volume(row.owned_volume_names[0])
    assert (present(await service.store.get(first.id))).session_api_key_hash is None
    native.fail_init = False
    assert await service.resume_sandbox(first.id)


@pytest.mark.asyncio
async def test_named_volume_collision_cannot_be_adopted(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    original_create = native.create_volume

    def create_foreign(name: str, labels: dict[str, str]) -> Volume:
        return original_create(name, {**labels, RESOURCE_LABEL: 'foreign'}).model

    native.volumes.create.side_effect = create_foreign
    with pytest.raises(SandboxError):
        await service.start_sandbox(sandbox_id='foreign-volume')
    volume = next(iter(native.volume_items.values()))
    volume.remove.assert_not_called()
    assert (
        present(await service.store.get('foreign-volume'))
    ).desired_state == 'cleanup'


def test_volume_initializer_accepts_numeric_uid_without_passwd_entry(
    tmp_path: Path,
) -> None:
    import os
    import subprocess

    from openhands.app_server.sandbox.managed_docker_sandbox_service import (
        _VOLUME_SETUP,
    )

    # Run the real command builder with stubbed filesystem commands; it must
    # not consult passwd for an explicitly numeric uid:gid or perform chown.
    binary_dir = tmp_path / 'bin'
    binary_dir.mkdir()
    for command in ('mkdir', 'chown'):
        file = binary_dir / command
        file.write_text('#!/bin/sh\nexit 0\n')
        file.chmod(0o755)
    lookup = binary_dir / 'id'
    lookup.write_text('#!/bin/sh\nexit 91\n')
    lookup.chmod(0o755)
    result = subprocess.run(
        [
            '/bin/sh',
            '-c',
            _VOLUME_SETUP,
            'volume-setup',
            '654321:654322',
            '/workspace',
            '/workspace/project',
        ],
        env={**os.environ, 'PATH': str(binary_dir)},
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_single_connection_pool_supports_cap_eviction_and_concurrent_starts(
    managed: ManagedFixture, test_database: TestDatabase
) -> None:
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    service, native = managed
    engine = create_async_engine(
        test_database.async_url, pool_size=1, max_overflow=0, pool_timeout=0.5
    )
    service = replace(
        service, store=replace(service.store, engine=engine), max_num_sandboxes=1
    )
    original_docker = service._docker

    async def docker_call(call: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        connection = service.store._current_connection()
        assert connection is None or not connection.in_transaction()
        return await original_docker(call, *args, **kwargs)

    try:
        with patch.object(service, '_docker', side_effect=docker_call):
            first, second = await asyncio.wait_for(
                asyncio.gather(service.start_sandbox(), service.start_sandbox()),
                timeout=5,
            )
        statuses = [
            present(item).status
            for item in (await service.batch_get_sandboxes([first.id, second.id]))
        ]
        assert statuses.count(SandboxStatus.PAUSED) == 1
        assert statuses.count(SandboxStatus.RUNNING) == 1
        assert len(native.init_posts) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_operation_connection_is_not_shared_with_child_tasks(
    managed: ManagedFixture,
) -> None:
    import asyncio

    service, _ = managed

    async def child_connection() -> AsyncConnection | None:
        return service.store._current_connection()

    async with service.store.lock('test-parent'):
        assert service.store._current_connection() is not None
        assert await asyncio.create_task(child_connection()) is None


@pytest.mark.asyncio
async def test_cancellation_releases_advisory_lock(managed: ManagedFixture) -> None:
    import asyncio

    service, _ = managed
    acquired = asyncio.Event()

    async def operation() -> None:
        async with service.store.lock('test-cancel'):
            acquired.set()
            await asyncio.sleep(100)

    task = asyncio.create_task(operation())
    await acquired.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with replace(service.store).lock('test-cancel'):
        pass


@pytest.mark.asyncio
async def test_failed_resume_still_counts_for_cap_admission(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    service.max_num_sandboxes = 1
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    row = present(await service.store.get(first.id))
    container = native.get(row.container_id)
    container.attrs['State']['Status'] = 'exited'
    native.fail_init = True
    with pytest.raises(SandboxError):
        await service.resume_sandbox(first.id)
    assert (present(await service.store.get(first.id))).desired_state == 'error'
    assert container.attrs['State']['Status'] == 'running'
    native.fail_init = False
    await service.start_sandbox()
    assert container.attrs['State']['Status'] == 'paused'


@pytest.mark.asyncio
async def test_unknown_or_dormant_status_never_publishes_credentials(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    native.init_state = 'dormant'
    info = present(await service.get_sandbox(first.id))
    assert info.status == SandboxStatus.STARTING
    assert info.session_api_key is None and info.exposed_urls is None
    assert len(native.init_posts) == 1


@pytest.mark.asyncio
async def test_pinned_sdk_router_auth_readiness_and_startup_cors(
    managed: ManagedFixture,
) -> None:
    from fastapi import Depends, FastAPI

    from openhands.agent_server.conversation_router import conversation_router
    from openhands.agent_server.dependencies import check_session_api_key
    from openhands.agent_server.init_router import InitStatus, init_router
    from openhands.agent_server.middleware import CORSDispatcher
    from openhands.agent_server.models import ConversationPage

    service, native = managed
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    row = present(await service.store.get(first.id))
    launch, _ = service.store.launch(row)
    app = FastAPI()
    app.state.config = SimpleNamespace(
        session_api_keys=[first.session_api_key],
        secret_key=service.store.credentials(row).workspace_key,
    )
    app.state.init_service = SimpleNamespace(snapshot=lambda: InitStatus(state='ready'))
    app.state.conversation_service = SimpleNamespace(
        search_conversations=AsyncMock(return_value=ConversationPage(items=[]))
    )
    app.include_router(init_router, prefix='/api')
    app.include_router(
        conversation_router,
        prefix='/api',
        dependencies=[Depends(check_session_api_key)],
    )
    app.add_middleware(
        CORSDispatcher,
        allow_origins=json.loads(
            native.created[-1]['environment']['OH_ALLOW_CORS_ORIGINS']
        ),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        service = replace(service, httpx_client=client)
        assert await service._ready(row, native.get(row.container_id).model)
        response = await client.options(
            'http://localhost:41000/api/conversations/search',
            headers={
                'Origin': 'http://frontend.local',
                'Access-Control-Request-Method': 'GET',
                'Access-Control-Request-Headers': 'X-Session-API-Key',
            },
        )
        assert response.status_code == 200
        assert (
            response.headers['access-control-allow-origin'] == 'http://frontend.local'
        )
        for invalid_key in (
            'static-bootstrap',
            launch.initial_env['OH_SESSION_API_KEYS_0'].get_secret_value(),
        ):
            response = present(
                await client.get(
                    'http://localhost:41000/api/conversations/search',
                    headers={'X-Session-API-Key': invalid_key},
                )
            )
            assert response.status_code == 401
        response = await client.post(
            'http://localhost:41000/api/init',
            json={},
            headers={'X-Init-API-Key': 'static-bootstrap'},
        )
        assert response.status_code == 401
        # Pin the distinction that caught the former readiness endpoint bug.
        response = present(
            await client.get(
                'http://localhost:41000/api/conversations',
                headers={'X-Session-API-Key': first.session_api_key},
            )
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_vscode_token_is_cli_safe_unique_durable_and_distinct_from_api_auth(
    managed: ManagedFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from urllib.parse import parse_qs, urlsplit

    from openhands.agent_server import vscode_service

    # URL-safe tokens can start with '-', which OpenVSCode treats as a CLI flag.
    token_urlsafe = secrets.token_urlsafe
    monkeypatch.setattr(secrets, 'token_urlsafe', lambda n: '-' + token_urlsafe(n))
    service, native = managed
    template = service.sandbox_spec_service.provider_config.templates[0]
    assert template.provider == 'docker'
    template.docker.ports['VSCODE'] = 8001
    template.initial_env['OH_ENABLE_VSCODE'] = SecretStr('true')
    first = await service.start_sandbox()
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    row = present(await service.store.get(first.id))
    launch, _ = service.store.launch(row)
    token = launch.initial_env['OH_SESSION_API_KEYS_0'].get_secret_value()
    assert not token.startswith('-')
    assert len(bytes.fromhex(token)) == 32
    vscode_url = next(url.url for url in first.exposed_urls if url.name == 'VSCODE')
    assert parse_qs(urlsplit(vscode_url).query)['tkn'] == [token]
    assert (
        len(
            {
                token,
                first.session_api_key,
                service.store.credentials(row).workspace_key.get_secret_value(),
            }
        )
        == 3
    )
    assert native.created[-1]['environment']['OH_VSCODE_PORT'] == '8001'
    with (
        patch.object(vscode_service, '_vscode_service', None),
        patch(
            'openhands.agent_server.config.get_default_config',
            return_value=SimpleNamespace(
                enable_vscode=True,
                session_api_keys=[token],
                vscode_port=8001,
                vscode_base_path=None,
            ),
        ),
    ):
        assert present(vscode_service.get_vscode_service()).connection_token == token
    container = native.get(row.container_id)
    container.attrs['State']['Status'] = 'exited'
    assert await service.resume_sandbox(first.id)
    resumed = present(await service.get_sandbox(first.id))
    assert resumed.session_api_key != first.session_api_key
    assert resumed.exposed_urls is not None
    assert (
        next(url.url for url in resumed.exposed_urls if url.name == 'VSCODE')
        == vscode_url
    )
    second = await service.start_sandbox()
    assert second.session_api_key is not None
    assert second.exposed_urls is not None
    second_launch, _ = service.store.launch(present(await service.store.get(second.id)))
    assert (
        second_launch.initial_env['OH_SESSION_API_KEYS_0'].get_secret_value() != token
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'baked_keys', [None, '[]', '["baked-first", "baked-second"]', '3']
)
async def test_startup_service_token_overrides_baked_image_credentials(
    managed: ManagedFixture, baked_keys: str | None
) -> None:
    from openhands.agent_server.env_parser import from_env

    service, native = managed
    info = await service.start_sandbox()
    assert info.session_api_key is not None
    assert info.exposed_urls is not None
    startup_env = native.created[-1]['environment']
    token = startup_env['OH_SESSION_API_KEYS_0']
    image_env = {
        'OH_SESSION_API_KEYS_0': 'baked-first',
        'OH_SESSION_API_KEYS_1': 'baked-second',
        'OH_SESSION_API_KEYS_2': 'baked-third',
    }
    if baked_keys is not None:
        image_env['OH_SESSION_API_KEYS'] = baked_keys
    # Docker's launch environment overrides image defaults. Exercise the real
    # pinned parser so empty/raw lists and leftover indexes cannot disable the
    # generated service token or add an image's static credentials.
    with patch.dict('os.environ', {**image_env, **startup_env}, clear=True):
        assert from_env(list[str], 'OH_SESSION_API_KEYS') == [token]
    launch, _ = service.store.launch(present(await service.store.get(info.id)))
    assert json.loads(launch.initial_env['OH_SESSION_API_KEYS'].get_secret_value()) == [
        token
    ]


@pytest.mark.asyncio
async def test_crash_before_container_creation_remains_starting_and_retry_is_explicit(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    first = await service.start_sandbox(sandbox_id='interrupted')
    assert first.session_api_key is not None
    assert first.exposed_urls is not None
    row = present(await service.store.get(first.id))
    native.items.pop(row.container_name)
    row.container_id = None
    row.initialized_generation = None
    row.initializing_generation = None
    row.session_api_key_hash = None
    row.desired_state = 'starting'
    await service.store.save(row)
    info = present(await service.get_sandbox(first.id))
    assert info.status == SandboxStatus.STARTING
    assert info.session_api_key is None and info.exposed_urls is None
    assert (
        present((await service.batch_get_sandboxes([first.id]))[0]).status
        == SandboxStatus.STARTING
    )
    with pytest.raises(SandboxError) as retry:
        await service.start_sandbox(sandbox_id=first.id)
    assert retry.value.status_code == 409
    assert await service.store.get(first.id) is not None
    assert await service.delete_sandbox(first.id)
    assert native.volume_items == {}


@pytest.mark.asyncio
async def test_startup_cors_combines_template_and_app_origins(
    managed: ManagedFixture,
) -> None:
    service, native = managed
    template = service.sandbox_spec_service.provider_config.templates[0]
    assert template.provider == 'docker'
    template.initial_env['OH_ALLOW_CORS_ORIGINS'] = SecretStr(
        '["https://template.example"]'
    )
    template.initial_env['OH_ALLOW_CORS_ORIGINS_0'] = SecretStr(
        'https://indexed.example'
    )
    await service.start_sandbox()
    expected = [
        'https://template.example',
        'https://indexed.example',
        'http://frontend.local',
        'http://other.local',
    ]
    assert (
        json.loads(native.created[-1]['environment']['OH_ALLOW_CORS_ORIGINS'])
        == expected
    )
    assert native.init_posts[-1][1]['allow_cors_origins'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resume', 'start_existing'])
@pytest.mark.parametrize('verified', [False, True])
async def test_managed_resume_requires_verified_direct_conversations(
    managed: ManagedFixture,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    verified: bool,
) -> None:
    service, native = managed
    info = await service.start_sandbox(sandbox_id='retained')
    assert info.session_api_key is not None
    assert info.exposed_urls is not None
    await service.pause_sandbox(info.id)
    posts_before = len(native.init_posts)
    row = present(await service.store.get(info.id))
    container = native.get(row.container_id)
    conversation = AppConversationInfo(
        created_by_user_id=None,
        sandbox_id=info.id,
        tags={'direct_llm_validated': '1'} if verified else {},
    )
    history = AsyncMock()
    history.search_app_conversation_info.return_value = AppConversationInfoPage(
        items=[conversation]
    )

    @asynccontextmanager
    async def conversation_history(state: InjectorState) -> AsyncIterator[AsyncMock]:
        yield history

    monkeypatch.setenv('ENABLE_LITELLM', 'false')
    monkeypatch.setattr(
        SandboxService, 'validate_resume_configuration', _VALIDATE_RESUME
    )
    monkeypatch.setattr(
        'openhands.app_server.config.get_app_conversation_info_service',
        conversation_history,
    )
    action = (
        service.resume_sandbox(info.id)
        if operation == 'resume'
        else service.start_sandbox(sandbox_id=info.id)
    )
    if verified:
        assert await action
        container.unpause.assert_called_once()
        assert container.attrs['State']['Status'] == 'running'
    else:
        with pytest.raises(LiteLLMIntegrationDisabled, match='older sandbox'):
            await action
        assert len(native.init_posts) == posts_before
        assert container.attrs['State']['Status'] == 'paused'
    history.search_app_conversation_info.assert_awaited_once_with(
        sandbox_id__eq=info.id, page_id=None, include_sub_conversations=True
    )


@pytest.mark.asyncio
async def test_managed_resume_checks_ownership_before_conversation_history(
    managed: ManagedFixture,
) -> None:
    service, _ = managed
    with patch.object(
        service, 'validate_resume_configuration', new_callable=AsyncMock
    ) as validate:
        assert await service.resume_sandbox('not-owned') is False
        validate.assert_not_awaited()
