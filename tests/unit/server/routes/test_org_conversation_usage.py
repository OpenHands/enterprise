"""HTTP tests for GET /api/organizations/{org_id}/conversations/{id}/usage.

These run the real route, ``require_permission`` (roles and memberships in
PostgreSQL), the real service injector and a real filesystem event store
holding pinned-SDK event objects.
"""

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from openhands.app_server.config import get_global_config
from openhands.app_server.event.filesystem_event_service import (
    FilesystemEventService,
    FilesystemEventServiceInjector,
)
from openhands.app_server.user_auth import get_user_id, get_user_settings_store
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.event.conversation_state import (
    FULL_STATE_KEY,
    ConversationStateUpdateEvent,
)
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.llm.utils.metrics import Metrics, MetricsSnapshot
from openhands.sdk.tool.builtins import FinishAction, FinishObservation
from openhands.tools.task.definition import TaskAction, TaskObservation
from server.routes import org_models
from server.routes.orgs import org_router
from server.services import org_conversation_usage_service
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.stored_conversation_metadata import StoredConversationMetadata
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas

SECRET_MARKERS = (
    'SECRET-PROMPT',
    'SECRET-DESCRIPTION',
    'SECRET-RESULT',
    'SECRET-TITLE',
    'SECRET-TAG',
    'SECRET-STATE',
    'SECRET-RUNTIME',
)
ROLE_RANKS = {'owner': 10, 'admin': 20, 'member': 1000, 'service_account': 2000}
T0 = datetime(2026, 5, 1, 12, 0, 0)


def _ts(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def _metrics(
    model: str,
    cost: float,
    prompt: int = 0,
    completion: int = 0,
    *,
    cache_read: int = 0,
    cache_write: int = 0,
    reasoning: int = 0,
) -> Metrics:
    metrics = Metrics(model_name=model)
    metrics.add_cost(cost)
    metrics.add_token_usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning,
        context_window=1000,
        response_id='r',
    )
    return metrics


def _stats_event(seconds: float, buckets: dict[str, Metrics]):
    return ConversationStateUpdateEvent(
        key='stats',
        value=ConversationStats(usage_to_metrics=buckets),
        timestamp=_ts(seconds),
    )


def _task_call(
    seconds: float,
    call_id: str,
    subagent_type: str = 'explore',
    resume: str | None = None,
) -> ActionEvent:
    return ActionEvent(
        thought=[],
        action=TaskAction(
            prompt='SECRET-PROMPT do the work',
            description='SECRET-DESCRIPTION',
            subagent_type=subagent_type,
            resume=resume,
        ),
        tool_name='task',
        tool_call_id=call_id,
        tool_call=MessageToolCall(
            id=call_id, name='task', arguments='{}', origin='completion'
        ),
        llm_response_id=f'resp-{call_id}',
        timestamp=_ts(seconds),
    )


def _task_result(
    seconds: float, action: ActionEvent, task_id: str, status: str = 'completed'
) -> ObservationEvent:
    return ObservationEvent(
        observation=TaskObservation.from_text(
            text='SECRET-RESULT output',
            task_id=task_id,
            subagent=action.action.subagent_type,  # type: ignore[union-attr]
            status=status,
            is_error=status == 'error',
        ),
        action_id=action.id,
        tool_name='task',
        tool_call_id=action.tool_call_id,
        timestamp=_ts(seconds),
    )


class _ApiKeyAuth:
    def __init__(self, org_id):
        self._org_id = org_id

    def get_api_key_org_id(self):
        return self._org_id


@pytest.fixture
def env(
    app_db_session,
    async_session_maker,
    session_maker,
    create_org,
    create_user,
    tmp_path,
    monkeypatch,
):
    """Two orgs with one member per role, the real route and a real event
    store. Runtime/sandbox access fails the test if anything reaches it."""
    config = get_global_config()
    monkeypatch.setattr(config, 'persistence_dir', tmp_path)
    monkeypatch.setattr(config, 'event', FilesystemEventServiceInjector())
    sandbox_guard = MagicMock(side_effect=AssertionError('sandbox access'))
    monkeypatch.setattr(
        'openhands.app_server.config.get_sandbox_service', sandbox_guard
    )
    monkeypatch.setattr(
        'openhands.app_server.config.get_app_conversation_service', sandbox_guard
    )
    for module in ('org_member_store', 'role_store', 'user_store', 'org_store'):
        monkeypatch.setattr(f'storage.{module}.a_session_maker', async_session_maker)

    with session_maker() as session:
        for name, rank in ROLE_RANKS.items():
            session.add(Role(name=name, rank=rank))
        session.commit()
        role_ids = dict(session.execute(text('SELECT name, id FROM role')).all())

    org = create_org(contact_name='c', contact_email='c@example.com')
    other_org = create_org(contact_name='c', contact_email='c@example.com')
    users = {}
    for role in ('owner', 'admin', 'member', 'service_account'):
        user = create_user(current_org_id=org.id)
        with session_maker() as session:
            session.add(
                OrgMember(
                    org_id=org.id,
                    user_id=user.id,
                    role_id=role_ids[role],
                    llm_api_key='k',
                    status='active',
                )
            )
            session.commit()
        users[role] = str(user.id)
    other_admin = create_user(current_org_id=other_org.id)
    with session_maker() as session:
        session.add(
            OrgMember(
                org_id=other_org.id,
                user_id=other_admin.id,
                role_id=role_ids['admin'],
                llm_api_key='k',
                status='active',
            )
        )
        session.commit()
    users['other_org_admin'] = str(other_admin.id)
    users['outsider'] = str(create_user().id)

    caller = {'user_id': users['admin'], 'api_key_org_id': None}
    app = FastAPI()
    app.include_router(org_router)
    app.dependency_overrides[get_user_id] = lambda: caller['user_id']
    app.dependency_overrides[get_user_settings_store] = lambda: None
    # Org update responses include LiteLLM credits; there is no LiteLLM here.
    monkeypatch.setattr(
        'storage.lite_llm_manager.LiteLlmManager.get_user_team_info',
        AsyncMock(return_value=None),
    )

    @app.middleware('http')
    async def _auth_state(request: Request, call_next):
        request.state.user_auth = _ApiKeyAuth(caller['api_key_org_id'])
        return await call_next(request)

    def add_conversation(
        *,
        org_id=org.id,
        creator=users['member'],
        events=(),
        deleted=False,
        version='V1',
        **columns,
    ):
        conversation_id = str(uuid.uuid4())
        values = dict(
            accumulated_cost=0.0,
            prompt_tokens=0,
            completion_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            reasoning_tokens=0,
            title='SECRET-TITLE',
            tags={'note': 'SECRET-TAG'},
            llm_model='main-model',
            agent_kind='openhands',
            execution_status='finished',
            sandbox_id='sandbox-1',
        )
        values.update(columns)
        with session_maker() as session:
            session.add(
                StoredConversationMetadata(
                    conversation_id=conversation_id,
                    conversation_version=version,
                    deleted_at=datetime.now(timezone.utc) if deleted else None,
                    **values,
                )
            )
            session.add(
                StoredConversationMetadataSaas(
                    conversation_id=conversation_id,
                    user_id=uuid.UUID(creator),
                    org_id=org_id,
                )
            )
            session.commit()
        save_events(creator, conversation_id, events)
        return conversation_id

    def save_events(user_id, conversation_id, events):
        store = FilesystemEventService(
            prefix=tmp_path,
            user_id=user_id,
            app_conversation_info_service=None,
            app_conversation_info_load_tasks={},
        )
        for event in events:
            asyncio.run(store.save_event(uuid.UUID(conversation_id), event))

    def get(conversation_id, as_role='admin', target_org=org.id, api_key_org=None):
        caller['user_id'] = users[as_role]
        caller['api_key_org_id'] = api_key_org
        return client.get(
            f'/api/organizations/{target_org}/conversations/{conversation_id}/usage'
        )

    def set_enabled(value, as_role='owner', target_org=org.id):
        caller['user_id'] = users[as_role]
        caller['api_key_org_id'] = None
        return client.patch(
            f'/api/organizations/{target_org}',
            json={'enable_conversation_usage_detail': value},
        )

    def is_enabled(org_id=org.id):
        with session_maker() as session:
            return session.get(Org, org_id).enable_conversation_usage_detail

    # One event loop for all requests: pooled DB connections are loop-bound.
    client = TestClient(app).__enter__()
    # Existing tests read with the org opted in through the real settings API.
    assert set_enabled(True).status_code == 200

    yield SimpleNamespace(
        org=org,
        set_enabled=set_enabled,
        is_enabled=is_enabled,
        other_org=other_org,
        users=users,
        get=get,
        add_conversation=add_conversation,
        save_events=save_events,
        sandbox_guard=sandbox_guard,
        app=app,
        caller=caller,
    )
    client.__exit__(None, None, None)


def _finished_task_history():
    """Main agent, condenser and task_00000001 started then resumed. The task
    bucket is cumulative across both calls (SDK replaces it on each end)."""
    start = _task_call(10, 'call-1')
    resume = _task_call(60, 'call-2', resume='task_00000001')
    snapshot = {
        'agent': _metrics(
            'main-model', 2.0, 1000, 200, cache_read=300, cache_write=50, reasoning=80
        ),
        'condenser': _metrics('cheap-model', 0.25, 400, 40),
        'task:task_00000001': _metrics('default', 0.75, 300, 30),
    }
    return [
        start,
        _task_result(40, start, 'task_00000001'),
        resume,
        _task_result(75, resume, 'task_00000001', 'error'),
        _stats_event(100, snapshot),
        # An older snapshot stored after the newest must not win.
        _stats_event(50, {'agent': _metrics('main-model', 1.0, 500, 100)}),
    ]


FINISHED_TOTALS = dict(
    accumulated_cost=3.0,
    prompt_tokens=1700,
    completion_tokens=270,
    cache_read_tokens=300,
    cache_write_tokens=50,
    reasoning_tokens=80,
)


def test_admin_reads_main_condenser_and_task_usage_of_another_user(env):
    conversation_id = env.add_conversation(
        events=_finished_task_history(), **FINISHED_TOTALS
    )

    response = env.get(conversation_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['completeness'] == 'full', body['incomplete_reasons']
    assert body['latest_snapshot_at'].startswith('2026-05-01T12:01:40')
    buckets = {b['usage_id']: b for b in body['buckets']}
    assert {k: (b['kind'], b['model']) for k, b in buckets.items()} == {
        'agent': ('main_agent', 'main-model'),
        'condenser': ('condenser', 'cheap-model'),
        'task:task_00000001': ('task', None),
    }
    assert buckets['agent']['usage'] == {
        'cost': 2.0,
        'prompt_tokens': 1000,
        'completion_tokens': 200,
        'cache_read_tokens': 300,
        'cache_write_tokens': 50,
        'reasoning_tokens': 80,
    }
    # One call per start/resume, correlated to the task's single bucket.
    assert [
        (
            c['task_id'],
            c['agent_type'],
            c['resumed'],
            c['status'],
            c['duration_seconds'],
        )
        for c in body['task_calls']
    ] == [
        ('task_00000001', 'explore', False, 'completed', 30.0),
        ('task_00000001', 'explore', True, 'error', 15.0),
    ]
    for marker in SECRET_MARKERS:
        assert marker not in response.text
    env.sandbox_guard.assert_not_called()


def test_events_are_read_from_the_creator_namespace_only(env):
    conversation_id = env.add_conversation(
        events=[_stats_event(1, {'agent': _metrics('creator-model', 1.0, 1, 1)})],
        accumulated_cost=1.0,
        prompt_tokens=1,
        completion_tokens=1,
    )
    env.save_events(
        env.users['admin'],
        conversation_id,
        [_stats_event(2, {'agent': _metrics('decoy-model', 9.0, 9, 9)})],
    )

    body = env.get(conversation_id).json()

    assert [b['model'] for b in body['buckets']] == ['creator-model']


def test_older_snapshot_buckets_are_not_merged_into_the_newest(env):
    conversation_id = env.add_conversation(
        events=[
            _stats_event(1, {'delegate:old': _metrics('m', 5.0, 5, 5)}),
            _stats_event(2, {'agent': _metrics('m', 1.0, 1, 1)}),
        ],
        accumulated_cost=1.0,
        prompt_tokens=1,
        completion_tokens=1,
    )

    body = env.get(conversation_id).json()

    assert [b['usage_id'] for b in body['buckets']] == ['agent']


def test_record_ahead_in_tokens_only_is_partial(env):
    conversation_id = env.add_conversation(
        events=[_stats_event(1, {'agent': _metrics('m', 1.0, 10, 1)})],
        accumulated_cost=1.0,
        prompt_tokens=50,
        completion_tokens=1,
    )

    body = env.get(conversation_id).json()

    assert body['completeness'] == 'partial'
    assert body['incomplete_reasons'] == ['totals_ahead_of_snapshot']


def test_running_task_without_synced_usage_is_partial(env):
    call = _task_call(10, 'call-1', subagent_type='code-reviewer')
    conversation_id = env.add_conversation(
        events=[call, _stats_event(20, {'agent': _metrics('m', 1.0, 1, 1)})],
        execution_status='running',
        accumulated_cost=1.0,
        prompt_tokens=1,
        completion_tokens=1,
    )

    body = env.get(conversation_id).json()

    assert body['completeness'] == 'partial'
    assert body['incomplete_reasons'] == ['task_usage_pending']
    [call] = body['task_calls']
    assert (call['task_id'], call['agent_type'], call['status']) == (
        None,
        'code-reviewer',
        'running',
    )
    assert call['duration_seconds'] is None


def test_finished_task_without_usage_bucket_is_partial(env):
    call = _task_call(10, 'call-1')
    conversation_id = env.add_conversation(
        events=[
            call,
            _task_result(20, call, 'task_00000002'),
            _stats_event(30, {'agent': _metrics('m', 1.0, 1, 1)}),
        ],
        accumulated_cost=1.0,
        prompt_tokens=1,
        completion_tokens=1,
    )

    body = env.get(conversation_id).json()

    assert body['incomplete_reasons'] == ['task_usage_missing']
    assert body['task_calls'][0]['task_id'] == 'task_00000002'


def test_task_bucket_without_retained_calls_is_partial(env):
    conversation_id = env.add_conversation(
        events=[_stats_event(1, {'task:task_00000003': _metrics('m', 1.0, 1, 1)})],
        accumulated_cost=1.0,
        prompt_tokens=1,
        completion_tokens=1,
    )

    body = env.get(conversation_id).json()

    assert body['incomplete_reasons'] == ['task_calls_outside_history']
    assert body['task_calls'] == []
    assert body['buckets'][0]['task_id'] == 'task_00000003'


def test_result_before_call_gives_unknown_duration(env):
    call = _task_call(50, 'call-1')
    conversation_id = env.add_conversation(
        events=[
            call,
            _task_result(40, call, 'task_00000001'),
            _stats_event(60, {'task:task_00000001': _metrics('m', 1.0, 1, 1)}),
        ],
        accumulated_cost=1.0,
        prompt_tokens=1,
        completion_tokens=1,
    )

    body = env.get(conversation_id).json()

    assert body['task_calls'][0]['duration_seconds'] is None
    assert body['incomplete_reasons'] == ['inconsistent_task_timestamps']


@pytest.mark.parametrize(
    ('role', 'expected'),
    [
        ('owner', 200),
        ('admin', 200),
        ('member', 403),
        ('service_account', 403),
        ('other_org_admin', 403),
        ('outsider', 403),
    ],
)
def test_only_same_org_owner_and_admin_may_read_usage(env, role, expected):
    conversation_id = env.add_conversation()

    response = env.get(conversation_id, as_role=role)

    assert response.status_code == expected


def test_member_is_denied_even_for_own_conversation(env):
    conversation_id = env.add_conversation(creator=env.users['member'])

    assert env.get(conversation_id, as_role='member').status_code == 403


def test_superadmin_without_org_role_is_denied(env, session_maker):
    """The superadmin super role does not carry this permission; only an
    Owner/Admin membership in the org grants it."""
    conversation_id = env.add_conversation()
    super_role = SimpleNamespace(name='admin')

    with patch(
        'server.auth.authorization.get_user_super_role',
        AsyncMock(return_value=super_role),
    ):
        response = env.get(conversation_id, as_role='outsider')

    assert response.status_code == 403


def test_other_org_admin_cannot_read_by_targeting_their_own_org(env):
    conversation_id = env.add_conversation(org_id=env.org.id)
    env.set_enabled(True, as_role='other_org_admin', target_org=env.other_org.id)

    response = env.get(
        conversation_id, as_role='other_org_admin', target_org=env.other_org.id
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ('api_key_org', 'expected'), [('same', 200), ('other', 403), (None, 200)]
)
def test_api_key_must_be_bound_to_the_requested_org(env, api_key_org, expected):
    conversation_id = env.add_conversation()
    bound = {'same': env.org.id, 'other': env.other_org.id, None: None}[api_key_org]

    response = env.get(conversation_id, api_key_org=bound)

    assert response.status_code == expected


def test_x_org_id_header_must_match_the_path_org(env):
    conversation_id = env.add_conversation()
    env.caller['user_id'] = env.users['admin']

    response = TestClient(env.app).get(
        f'/api/organizations/{env.org.id}/conversations/{conversation_id}/usage',
        headers={'X-Org-Id': str(env.other_org.id)},
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    'case', ['other_org', 'guessed', 'not_a_uuid', 'deleted', 'v0']
)
def test_unreachable_conversations_are_indistinguishable_from_absent(env, case):
    history = [_stats_event(1, {'agent': _metrics('m', 1.0, 1, 1)})]
    conversation_id = {
        'other_org': lambda: env.add_conversation(
            org_id=env.other_org.id,
            creator=env.users['other_org_admin'],
            events=history,
        ),
        'guessed': lambda: str(uuid.uuid4()),
        'not_a_uuid': lambda: 'not-a-uuid',
        'deleted': lambda: env.add_conversation(deleted=True, events=history),
        'v0': lambda: env.add_conversation(version='V0', events=history),
    }[case]()

    response = env.get(conversation_id)

    assert response.status_code == 404
    assert response.json() == {'detail': 'Conversation not found'}


@pytest.mark.parametrize('execution_status', ['running', 'paused', 'finished'])
def test_no_history_falls_back_to_totals_without_runtime_access(env, execution_status):
    conversation_id = env.add_conversation(
        execution_status=execution_status,
        sandbox_id='live-sandbox',
        accumulated_cost=1.5,
        prompt_tokens=10,
        completion_tokens=5,
    )

    response = env.get(conversation_id)

    body = response.json()
    assert response.status_code == 200
    assert body['completeness'] == 'totals_only'
    assert body['totals']['cost'] == 1.5
    assert body['incomplete_reasons'] == ['no_stats_snapshot']
    assert body['buckets'] == []
    assert body['task_calls'] == []
    env.sandbox_guard.assert_not_called()


def test_storage_errors_are_not_reported_as_missing_history(env, monkeypatch):
    conversation_id = env.add_conversation(accumulated_cost=2.0)

    async def broken(*args, **kwargs):
        raise OSError('storage unavailable')

    monkeypatch.setattr(FilesystemEventService, 'search_events', broken)
    env.caller['user_id'] = env.users['admin']
    response = TestClient(env.app, raise_server_exceptions=False).get(
        f'/api/organizations/{env.org.id}/conversations/{conversation_id}/usage'
    )

    assert response.status_code == 500


def test_record_ahead_of_retained_snapshot_is_partial(env):
    conversation_id = env.add_conversation(
        events=[_stats_event(1, {'agent': _metrics('m', 1.0, 10, 1)})],
        accumulated_cost=4.0,
    )

    body = env.get(conversation_id).json()

    assert body['completeness'] == 'partial'
    assert 'totals_ahead_of_snapshot' in body['incomplete_reasons']
    assert body['totals']['cost'] == 4.0


def test_bucket_without_token_usage_reports_unknown_tokens(env):
    conversation_id = env.add_conversation(
        # Wire form of a MetricsSnapshot whose token usage is unset.
        events=[
            ConversationStateUpdateEvent(
                key='stats',
                value={
                    'usage_to_metrics': {
                        'agent': MetricsSnapshot(
                            model_name='m', accumulated_cost=0.5
                        ).model_dump(mode='json')
                    }
                },
                timestamp=_ts(1),
            )
        ],
        accumulated_cost=0.5,
    )

    body = env.get(conversation_id).json()
    bucket = body['buckets'][0]
    assert body['completeness'] == 'partial'
    assert body['incomplete_reasons'] == ['unknown_counters']

    assert bucket['usage'] == {
        'cost': 0.5,
        'prompt_tokens': None,
        'completion_tokens': None,
        'cache_read_tokens': None,
        'cache_write_tokens': None,
        'reasoning_tokens': None,
    }


def test_task_scan_cap_is_flagged(env, monkeypatch):
    monkeypatch.setattr(org_conversation_usage_service, 'EVENT_PAGE_SIZE', 1)
    monkeypatch.setattr(org_conversation_usage_service, 'MAX_EVENTS_PER_KIND', 1)
    conversation_id = env.add_conversation(
        events=_finished_task_history(),
        accumulated_cost=3.6,
    )

    body = env.get(conversation_id).json()

    assert body['completeness'] == 'partial'
    assert 'scan_limit_reached' in body['incomplete_reasons']
    # Usage for the task is still known from its bucket.
    task_bucket = next(b for b in body['buckets'] if b['task_id'] == 'task_00000001')
    assert task_bucket['usage']['cost'] == 0.75


def test_time_budget_is_flagged(env, monkeypatch):
    monkeypatch.setattr(org_conversation_usage_service, 'SCAN_TIME_BUDGET_SECONDS', 0.0)
    conversation_id = env.add_conversation(
        events=_finished_task_history(),
        accumulated_cost=3.6,
    )

    body = env.get(conversation_id).json()

    assert body['completeness'] == 'totals_only'
    assert 'scan_limit_reached' in body['incomplete_reasons']
    assert body['buckets'] == []


def test_stalled_history_read_is_cut_by_the_time_budget(env, monkeypatch):
    monkeypatch.setattr(org_conversation_usage_service, 'SCAN_TIME_BUDGET_SECONDS', 0.2)
    conversation_id = env.add_conversation(
        events=_finished_task_history(), **FINISHED_TOTALS
    )
    stalled = []

    async def stall(*args, **kwargs):
        stalled.append(kwargs['kind__eq'])
        await asyncio.sleep(30)

    monkeypatch.setattr(FilesystemEventService, 'search_events', stall)
    started = time.monotonic()
    body = env.get(conversation_id).json()

    assert time.monotonic() - started < 5
    assert stalled
    assert body['completeness'] == 'totals_only'
    assert 'scan_limit_reached' in body['incomplete_reasons']
    assert body['totals']['cost'] == 3.0


def test_task_ids_and_agent_types_from_history_are_validated(env):
    bad = _task_call(1, 'call-x', subagent_type='Summarize SECRET-DESCRIPTION')
    conversation_id = env.add_conversation(
        events=[
            bad,
            _task_result(2, bad, 'SECRET-DESCRIPTION'),
            _stats_event(3, {'task:SECRET DESCRIPTION': _metrics('m', 1.0, 1, 1)}),
        ],
        accumulated_cost=1.0,
    )

    response = env.get(conversation_id)

    body = response.json()
    assert body['task_calls'][0]['task_id'] is None
    assert body['task_calls'][0]['agent_type'] is None
    assert body['buckets'][0]['usage_id'] is None
    assert body['buckets'][0]['kind'] == 'task'
    assert 'SECRET' not in response.text


def test_response_schema_is_an_explicit_allowlist(env):
    schema = env.app.openapi()['components']['schemas']

    assert set(schema['OrgConversationUsageResponse']['properties']) == {
        'conversation_id',
        'totals',
        'completeness',
        'incomplete_reasons',
        'latest_snapshot_at',
        'buckets',
        'task_calls',
    }
    assert set(schema['ConversationTaskCall']['properties']) == {
        'task_id',
        'agent_type',
        'resumed',
        'status',
        'started_at',
        'ended_at',
        'duration_seconds',
    }
    assert set(schema['ConversationUsageBucket']['properties']) == {
        'usage_id',
        'kind',
        'task_id',
        'model',
        'usage',
    }
    for model in (
        org_models.OrgConversationUsageResponse,
        org_models.ConversationTaskCall,
        org_models.ConversationUsageBucket,
        org_models.ConversationUsageCounters,
    ):
        assert model.model_config.get('extra') == 'forbid'


def test_stats_inside_a_full_state_snapshot_are_used(env):
    stats = ConversationStats(
        usage_to_metrics={'agent': _metrics('m', 1.25, 20, 2)}
    ).model_dump(mode='json', context={'use_snapshot': True})
    conversation_id = env.add_conversation(
        events=[
            ConversationStateUpdateEvent(
                key=FULL_STATE_KEY,
                value={'stats': stats, 'secret_registry': 'SECRET-STATE'},
                timestamp=_ts(1),
            )
        ],
        accumulated_cost=1.25,
    )

    response = env.get(conversation_id)

    assert response.json()['completeness'] == 'full'
    assert response.json()['buckets'][0]['usage']['cost'] == 1.25
    assert 'SECRET' not in response.text


def test_agent_type_is_kept_when_a_resume_sorts_first(env):
    start = _task_call(10, 'call-1').model_copy(update={'id': 'b-start'})
    resume = _task_call(
        60, 'call-2', subagent_type='not valid', resume='task_00000001'
    ).model_copy(update={'id': 'a-resume'})
    conversation_id = env.add_conversation(
        events=[
            start,
            _task_result(20, start, 'task_00000001'),
            resume,
            _stats_event(70, {'task:task_00000001': _metrics('m', 1.0, 1, 1)}),
        ],
        accumulated_cost=1.0,
        prompt_tokens=1,
        completion_tokens=1,
    )

    calls = env.get(conversation_id).json()['task_calls']

    assert [(c['agent_type'], c['resumed']) for c in calls] == [
        ('explore', False),
        ('explore', True),
    ]


def test_resume_only_history_uses_the_result_agent_type(env):
    resume = _task_call(10, 'call-1', resume='task_00000001')
    result = _task_result(20, resume, 'task_00000001')
    resume = resume.model_copy(
        update={'action': resume.action.model_copy(update={'subagent_type': 'x y'})}
    )
    conversation_id = env.add_conversation(
        events=[
            resume,
            result,
            _stats_event(30, {'task:task_00000001': _metrics('m', 1.0, 1, 1)}),
        ],
        accumulated_cost=1.0,
        prompt_tokens=1,
        completion_tokens=1,
    )

    [call] = env.get(conversation_id).json()['task_calls']

    assert call['agent_type'] == 'explore'
    assert call['resumed'] is True


def test_usage_detail_is_off_until_the_org_opts_in(env, create_org, monkeypatch):
    # A new org row, like an existing org after the migration, reads disabled.
    assert create_org().enable_conversation_usage_detail is False
    conversation_id = env.add_conversation(
        events=_finished_task_history(), **FINISHED_TOTALS
    )
    assert env.set_enabled(False).status_code == 200
    reads = MagicMock(side_effect=AssertionError('history read'))
    monkeypatch.setattr(FilesystemEventService, 'search_events', reads)

    responses = [
        env.get(cid, as_role=role)
        for cid in (conversation_id, '00000000-0000-4000-8000-000000000000', 'x')
        for role in ('owner', 'admin')
    ]

    assert {r.status_code for r in responses} == {403}
    assert len({r.text for r in responses}) == 1
    assert 'not enabled' in responses[0].json()['detail']
    reads.assert_not_called()
    env.sandbox_guard.assert_not_called()


def test_admin_enable_and_disable_take_effect_on_the_next_request(env):
    conversation_id = env.add_conversation(
        events=_finished_task_history(), **FINISHED_TOTALS
    )
    assert (
        env.set_enabled(False, as_role='admin').json()[
            'enable_conversation_usage_detail'
        ]
        is False
    )
    assert env.get(conversation_id).status_code == 403

    response = env.set_enabled(True, as_role='admin')

    assert response.json()['enable_conversation_usage_detail'] is True
    assert env.is_enabled() is True
    assert env.get(conversation_id).json()['totals']['cost'] == 3.0
    assert env.set_enabled(False).status_code == 200
    assert env.is_enabled() is False
    assert env.get(conversation_id).status_code == 403


@pytest.mark.parametrize('role', ['member', 'service_account', 'other_org_admin'])
def test_only_org_owner_or_admin_can_change_the_opt_in(env, role):
    env.set_enabled(False)

    assert env.set_enabled(True, as_role=role).status_code == 403
    assert env.is_enabled() is False


@pytest.mark.parametrize('value', ['true', 1, 'yes', None])
def test_opt_in_requires_a_real_boolean(env, value):
    env.set_enabled(False)

    response = env.set_enabled(value)

    if value is None:
        # Omitted/null means "no change", as for every other org field.
        assert response.status_code == 200
    else:
        assert response.status_code == 422
    assert env.is_enabled() is False


def test_opt_in_is_scoped_to_one_org(env):
    other_conversation = env.add_conversation(
        org_id=env.other_org.id,
        creator=env.users['other_org_admin'],
        events=_finished_task_history(),
    )

    assert env.is_enabled(env.other_org.id) is False
    response = env.get(
        other_conversation, as_role='other_org_admin', target_org=env.other_org.id
    )
    assert response.status_code == 403


def test_toggling_keeps_unrelated_org_settings(env, session_maker):
    with session_maker() as session:
        org = session.get(Org, env.org.id)
        org.enable_proactive_conversation_starters = False
        org.max_budget_per_task = 7.5
        org.conversation_expiration = 42
        name = org.name
        session.commit()

    assert env.set_enabled(False).status_code == 200

    with session_maker() as session:
        org = session.get(Org, env.org.id)
        assert (
            org.name,
            org.enable_proactive_conversation_starters,
            org.max_budget_per_task,
            org.conversation_expiration,
            org.enable_conversation_usage_detail,
        ) == (name, False, 7.5, 42, False)


def test_acp_managed_usage_is_main_agent_usage(env):
    conversation_id = env.add_conversation(
        events=[_stats_event(1, {'acp-managed': _metrics('acp-model', 1.5, 10, 2)})],
        accumulated_cost=1.5,
        prompt_tokens=10,
        completion_tokens=2,
    )

    body = env.get(conversation_id).json()

    assert [(b['usage_id'], b['kind']) for b in body['buckets']] == [
        ('acp-managed', 'main_agent')
    ]
    assert body['completeness'] == 'full'


def _count_loads(env, conversation_id, monkeypatch):
    # The store builds its kind index once per conversation by loading every
    # event; warm it so only this endpoint's reads are counted.
    loads: dict[str, int] = {}
    load = FilesystemEventService._load_event

    def counting(self, path):
        event = load(self, path)
        loads[type(event).__name__] = loads.get(type(event).__name__, 0) + 1
        return event

    env.get(conversation_id)
    monkeypatch.setattr(FilesystemEventService, '_load_event', counting)
    return loads


def _tool_noise(start: float, count: int):
    events = []
    for i in range(count):
        action = ActionEvent(
            thought=[],
            action=FinishAction(message='SECRET-RESULT'),
            tool_name='finish',
            tool_call_id=f'noise-{i}',
            tool_call=MessageToolCall(
                id=f'noise-{i}', name='finish', arguments='{}', origin='completion'
            ),
            llm_response_id=f'resp-noise-{i}',
            timestamp=_ts(start + i),
        )
        events += [
            action,
            ObservationEvent(
                observation=FinishObservation.from_text(text='SECRET-RESULT'),
                action_id=action.id,
                tool_name='finish',
                tool_call_id=action.tool_call_id,
                timestamp=_ts(start + i + 0.5),
            ),
        ]
    return events


def test_reads_load_only_the_events_they_need(env, monkeypatch):
    older = [
        _stats_event(i, {'agent': _metrics('m', 0.1 * i, i, 1)}) for i in range(1, 40)
    ]
    conversation_id = env.add_conversation(
        events=[
            *older,
            *_tool_noise(100, 60),
            _stats_event(500, {'agent': _metrics('m', 9.0, 90, 9)}),
        ],
        accumulated_cost=9.0,
        prompt_tokens=90,
        completion_tokens=9,
    )
    loads = _count_loads(env, conversation_id, monkeypatch)

    body = env.get(conversation_id).json()

    assert body['buckets'][0]['usage']['cost'] == 9.0
    assert body['completeness'] == 'full'
    # One small state page; all actions; no observations once no task call
    # was found in the complete action scan.
    assert loads == {
        'ConversationStateUpdateEvent': org_conversation_usage_service.STATE_PAGE_SIZE,
        'ActionEvent': 60,
    }


def test_task_result_scan_stops_once_every_call_is_resolved(env, monkeypatch):
    conversation_id = env.add_conversation(
        events=[
            *_tool_noise(0, 50),
            *_finished_task_history()[:4],
            *_tool_noise(200, 50),
        ],
        accumulated_cost=0.0,
    )
    loads = _count_loads(env, conversation_id, monkeypatch)

    calls = env.get(conversation_id).json()['task_calls']

    assert [c['status'] for c in calls] == ['completed', 'error']
    # One page (newest noise plus both task results) of 102 observations.
    assert loads['ObservationEvent'] == org_conversation_usage_service.EVENT_PAGE_SIZE


def test_interrupted_stats_scan_is_not_reported_as_missing_history(env, monkeypatch):
    monkeypatch.setattr(org_conversation_usage_service, 'STATE_PAGE_SIZE', 1)
    monkeypatch.setattr(org_conversation_usage_service, 'MAX_EVENTS_PER_KIND', 2)
    conversation_id = env.add_conversation(
        events=[
            _stats_event(1, {'agent': _metrics('m', 1.0, 1, 1)}),
            *[
                ConversationStateUpdateEvent(
                    key='execution_status', value='paused', timestamp=_ts(10 + i)
                )
                for i in range(3)
            ],
        ],
        accumulated_cost=1.0,
    )

    body = env.get(conversation_id).json()

    assert body['completeness'] == 'totals_only'
    assert body['incomplete_reasons'] == ['scan_limit_reached']
