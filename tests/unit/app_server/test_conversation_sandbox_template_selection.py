"""Conversation template overrides constrain reuse without changing grouping policy."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import bindparam, select
from sqlalchemy.dialects.postgresql import dialect

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
    AppConversationStartRequest,
    AppConversationStartTask,
)
from openhands.app_server.app_conversation.sql_app_conversation_start_task_service import (
    StoredAppConversationStartTask,
)
from openhands.app_server.sandbox.sandbox_models import (
    SandboxInfo,
    SandboxPage,
    SandboxStatus,
)
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.settings.settings_models import SandboxGroupingStrategy
from tests.unit.app_server.fixture_assertions import present
from tests.unit.app_server.service_mock_fixtures import (
    MockedConversationService,
    make_conversation_service,
)


def sandbox(
    id: str,
    spec: str = 'python',
    *,
    owner: str = 'user',
    age: int = 0,
    status: SandboxStatus = SandboxStatus.RUNNING,
) -> SandboxInfo:
    return SandboxInfo(
        id=id,
        created_by_user_id=owner,
        sandbox_spec_id=spec,
        status=status,
        session_api_key='session',
        created_at=datetime.now(UTC) - timedelta(days=age),
    )


@dataclass
class TemplateFixture:
    service: MockedConversationService
    grouping: AsyncMock
    counts: AsyncMock


@pytest.fixture
def service_fixture() -> Iterator[TemplateFixture]:
    instance = make_conversation_service()
    instance.user_context.get_user_id.return_value = 'user'
    grouping = AsyncMock(return_value=SandboxGroupingStrategy.ADD_TO_ANY)
    counts = AsyncMock(return_value={})
    instance.sandbox_service.start_sandbox.return_value = sandbox('new')
    instance.sandbox_service.search_sandboxes.return_value = SandboxPage(items=[])
    instance.sandbox_spec_service.get_sandbox_spec.return_value = SandboxSpecInfo(
        id='python', command=None
    )
    with (
        patch.object(instance, '_get_sandbox_grouping_strategy', grouping),
        patch.object(instance, '_get_conversation_counts_by_sandbox', counts),
    ):
        yield TemplateFixture(instance, grouping, counts)


def test_template_and_sandbox_id_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match='mutually exclusive'):
        AppConversationStartRequest(sandbox_id='existing', sandbox_spec_id='python')
    with pytest.raises(ValidationError):
        AppConversationStartRequest(sandbox_spec_id='')


def test_template_survives_existing_start_task_json_column() -> None:
    # Exercise the PostgreSQL bind codec and model validation used when tasks load.
    request = AppConversationStartRequest(sandbox_spec_id='python')
    column_type = StoredAppConversationStartTask.__table__.c.request.type
    compiled = select(bindparam('request', value=request, type_=column_type)).compile(
        dialect=dialect()
    )
    processor = column_type.bind_processor(dialect())
    assert processor is not None
    payload = processor(compiled.params['request'])
    restored = AppConversationStartRequest.model_validate_json(payload)
    assert restored.sandbox_spec_id == 'python'
    assert restored.sandbox_id is None


@pytest.mark.parametrize(
    'strategy, expected',
    [
        (SandboxGroupingStrategy.NO_GROUPING, None),
        (SandboxGroupingStrategy.ADD_TO_ANY, 'older'),
        (SandboxGroupingStrategy.GROUP_BY_NEWEST, 'newer'),
        (SandboxGroupingStrategy.LEAST_RECENTLY_USED, 'older'),
        (SandboxGroupingStrategy.FEWEST_CONVERSATIONS, 'newer'),
    ],
)
async def test_all_grouping_strategies_only_consider_matching_template(
    service_fixture: TemplateFixture, strategy: SandboxGroupingStrategy, expected: str
) -> None:
    service = service_fixture.service
    service_fixture.grouping.return_value = strategy
    service_fixture.counts.return_value = {
        'older': 2,
        'newer': 1,
        'full': 3,
    }
    service.sandbox_service.search_sandboxes.return_value = SandboxPage(
        items=[
            sandbox('wrong-template', 'other'),
            sandbox('foreign', owner='another-user'),
            sandbox('paused', status=SandboxStatus.PAUSED),
            sandbox('unknown', status=SandboxStatus.UNKNOWN),
            sandbox('older', age=5),
            sandbox('newer', age=1),
            sandbox('full'),
        ]
    )
    result = await service._find_running_sandbox_for_user('python')
    assert (result.id if result else None) == expected
    if strategy == SandboxGroupingStrategy.NO_GROUPING:
        service.sandbox_service.search_sandboxes.assert_not_awaited()
    else:
        service_fixture.counts.assert_awaited_once_with(['older', 'newer', 'full'])


@pytest.mark.parametrize('strategy', list(SandboxGroupingStrategy))
async def test_no_matching_template_starts_selected_template_for_every_strategy(
    service_fixture: TemplateFixture, strategy: SandboxGroupingStrategy
) -> None:
    service = service_fixture.service
    service_fixture.grouping.return_value = strategy
    service.sandbox_service.search_sandboxes.return_value = SandboxPage(
        items=[sandbox('mismatched', 'other')]
    )
    conversation_id = uuid4()
    task = AppConversationStartTask(
        created_by_user_id='user',
        request=AppConversationStartRequest(
            sandbox_spec_id='python', conversation_id=conversation_id
        ),
    )
    result = [item async for item in service._wait_for_sandbox_start(task)]
    service.sandbox_service.start_sandbox.assert_awaited_once_with(
        sandbox_spec_id='python', sandbox_id=conversation_id.hex
    )
    assert result[0].sandbox_id == 'new'
    assert result[0].request.sandbox_spec_id == 'python'
    assert result[0].request.sandbox_id is None


async def test_matching_grouped_sandbox_is_reused(
    service_fixture: TemplateFixture,
) -> None:
    service = service_fixture.service
    service.sandbox_service.search_sandboxes.return_value = SandboxPage(
        items=[sandbox('wrong', 'other'), sandbox('matching')]
    )
    task = AppConversationStartTask(
        created_by_user_id='user',
        request=AppConversationStartRequest(sandbox_spec_id='python'),
    )
    [item async for item in service._wait_for_sandbox_start(task)]
    assert task.sandbox_id == 'matching'
    service.sandbox_service.start_sandbox.assert_not_awaited()


async def test_full_matching_sandbox_starts_new_selected_template(
    service_fixture: TemplateFixture,
) -> None:
    service = service_fixture.service
    service.sandbox_service.search_sandboxes.return_value = SandboxPage(
        items=[sandbox('full')]
    )
    service_fixture.counts.return_value = {'full': 3}
    task = AppConversationStartTask(
        created_by_user_id='user',
        request=AppConversationStartRequest(sandbox_spec_id='python'),
    )
    [item async for item in service._wait_for_sandbox_start(task)]
    service.sandbox_service.start_sandbox.assert_awaited_once_with(
        sandbox_spec_id='python', sandbox_id=None
    )


async def test_matching_template_on_later_inventory_page(
    service_fixture: TemplateFixture,
) -> None:
    service = service_fixture.service
    service.sandbox_service.search_sandboxes.side_effect = [
        SandboxPage(items=[sandbox('wrong', 'other')], next_page_id='next'),
        SandboxPage(items=[sandbox('matching')]),
    ]
    assert (
        present(await service._find_running_sandbox_for_user('python')).id == 'matching'
    )
    assert service.sandbox_service.search_sandboxes.await_count == 2


async def test_invalid_explicit_template_fails_before_grouping_or_capacity_changes(
    service_fixture: TemplateFixture,
) -> None:
    service = service_fixture.service
    service.sandbox_spec_service.get_sandbox_spec.return_value = None
    task = AppConversationStartTask(
        created_by_user_id='user',
        request=AppConversationStartRequest(sandbox_spec_id='missing'),
    )
    with pytest.raises(ValueError, match='not found'):
        [item async for item in service._wait_for_sandbox_start(task)]
    service.sandbox_service.search_sandboxes.assert_not_awaited()
    service.sandbox_service.start_sandbox.assert_not_awaited()
    service.sandbox_service.pause_old_sandboxes.assert_not_awaited()


async def test_omitted_template_keeps_existing_grouping_behavior(
    service_fixture: TemplateFixture,
) -> None:
    service = service_fixture.service
    service.sandbox_service.search_sandboxes.return_value = SandboxPage(
        items=[sandbox('different-user-default', 'other')]
    )
    task = AppConversationStartTask(
        created_by_user_id='user', request=AppConversationStartRequest()
    )
    [item async for item in service._wait_for_sandbox_start(task)]
    assert task.sandbox_id == 'different-user-default'
    service.sandbox_spec_service.get_sandbox_spec.assert_not_awaited()
    service.sandbox_service.start_sandbox.assert_not_awaited()


async def test_omitted_template_keeps_provider_default_resolution(
    service_fixture: TemplateFixture,
) -> None:
    service = service_fixture.service
    task = AppConversationStartTask(
        created_by_user_id='user', request=AppConversationStartRequest()
    )
    [item async for item in service._wait_for_sandbox_start(task)]
    service.sandbox_service.start_sandbox.assert_awaited_once_with(sandbox_id=None)
    service.sandbox_spec_service.get_sandbox_spec.assert_not_awaited()


@pytest.mark.parametrize('template', ['python', None])
def test_parent_inheritance_respects_explicit_template_and_preserves_other_settings(
    service_fixture: TemplateFixture, template: str | None
) -> None:
    service = service_fixture.service
    request = AppConversationStartRequest(sandbox_spec_id=template)
    parent = AppConversationInfo(
        created_by_user_id='user',
        sandbox_id='parent-sandbox',
        selected_repository='org/repo',
        selected_branch='main',
        git_provider=None,
        llm_model='parent-model',
    )
    service._inherit_configuration_from_parent(request, parent)
    assert request.sandbox_id == (None if template else 'parent-sandbox')
    assert request.sandbox_spec_id == template
    assert request.selected_repository == 'org/repo'
    assert request.selected_branch == 'main'
    assert request.llm_model == 'parent-model'
    # Revalidate persisted request, which catches in-place parent conflicts too.
    AppConversationStartRequest.model_validate_json(request.model_dump_json())


@pytest.mark.parametrize(
    'status',
    [
        SandboxStatus.UNKNOWN,
        SandboxStatus.PAUSED,
        SandboxStatus.MISSING,
        SandboxStatus.ERROR,
        SandboxStatus.STARTING,
    ],
)
async def test_conversation_page_never_fans_out_to_nonrunning_sandboxes(
    service_fixture: TemplateFixture, status: SandboxStatus
) -> None:
    service = service_fixture.service
    first_id, second_id = uuid4(), uuid4()
    stored = [
        AppConversationInfo(
            id=first_id, sandbox_id='shared', created_by_user_id='user'
        ),
        AppConversationInfo(
            id=second_id, sandbox_id='shared', created_by_user_id='user'
        ),
    ]
    service.sandbox_service.batch_get_sandboxes.return_value = [
        sandbox('shared', status=status)
    ]
    with patch.object(
        service, '_get_live_conversation_info', new_callable=AsyncMock
    ) as live:
        result = await service._build_app_conversations(stored)
    assert [present(info).id for info in result] == [first_id, second_id]
    assert all(present(info).sandbox_status == status for info in result)
    service.sandbox_service.batch_get_sandboxes.assert_awaited_once_with(['shared'])
    live.assert_not_awaited()
