"""Known spend survives teardown without relying on asynchronous refresh."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from openhands.agent_server.models import ConversationInfo
from openhands.app_server import config
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationSortOrder,
)
from openhands.app_server.sandbox import final_metrics
from openhands.sdk import LLM, Agent, ConversationStats
from openhands.sdk.llm.utils.metrics import Metrics
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def fixture(monkeypatch):
    conversation_id = uuid4()
    context = object()
    persisted = {conversation_id: 0.0}
    db_active = []
    info = SimpleNamespace(id=conversation_id, sandbox_id='owned-sandbox')
    store = AsyncMock()
    store.search_app_conversation_info.return_value = SimpleNamespace(
        items=[info], next_page_id=None
    )

    async def update(cid, stats):
        assert db_active
        persisted[cid] = stats.get_combined_metrics().accumulated_cost

    store.update_conversation_statistics.side_effect = update

    @asynccontextmanager
    async def info_context(state):
        assert state.user_context is context
        yield store

    @asynccontextmanager
    async def db_context(state):
        assert state.user_context is context
        db_active.append(True)
        try:
            yield None
        finally:
            db_active.pop()

    monkeypatch.setattr(config, 'get_app_conversation_info_service', info_context)
    monkeypatch.setattr(config, 'get_db_session', db_context)
    metrics = Metrics(model_name='synthetic')
    metrics.add_cost(0.04)
    # Include a second usage bucket: the writer must receive the whole registry,
    # not only "agent" or the automation callback's scalar total.
    title = Metrics(model_name='synthetic-title')
    title.add_cost(0.01)
    stats = ConversationStats(usage_to_metrics={'agent': metrics, 'title': title})
    return SimpleNamespace(
        cid=conversation_id,
        context=context,
        persisted=persisted,
        db_active=db_active,
        store=store,
        info=info,
        payload=ConversationInfo(
            id=conversation_id,
            workspace=LocalWorkspace(working_dir='/workspace'),
            agent=Agent(llm=LLM(model='test-model')),
            stats=stats,
        ).model_dump(mode='json'),
        runtime={
            'url': 'https://owned-runtime.test/',
            'session_api_key': 'synthetic-key',
        },
    )


@pytest.mark.asyncio
async def test_persists_all_usage_before_runtime_can_disappear(fixture):
    async def agent_server(request):
        assert not fixture.db_active  # No DB connection across network I/O.
        assert request.url.path == f'/api/conversations/{fixture.cid.hex}'
        assert request.headers['X-Session-API-Key'] == 'synthetic-key'
        return httpx.Response(200, json=fixture.payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(agent_server)) as client:
        await final_metrics.persist_sandbox_final_metrics(
            'owned-sandbox', fixture.runtime, fixture.context, client
        )
    assert fixture.persisted[fixture.cid] == pytest.approx(0.05)
    fixture.store.search_app_conversation_info.assert_awaited_once()
    assert (
        fixture.store.search_app_conversation_info.call_args.kwargs['sandbox_id__eq']
        == 'owned-sandbox'
    )
    assert (
        fixture.store.search_app_conversation_info.call_args.kwargs[
            'include_sub_conversations'
        ]
        is True
    )
    assert (
        fixture.store.search_app_conversation_info.call_args.kwargs['sort_order']
        == AppConversationSortOrder.CREATED_AT
    )


@pytest.mark.asyncio
async def test_already_deleted_conversation_preserves_saved_amount(fixture):
    fixture.persisted[fixture.cid] = 0.02
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as client:
        await final_metrics.persist_sandbox_final_metrics(
            'owned-sandbox', fixture.runtime, fixture.context, client
        )
    assert fixture.persisted[fixture.cid] == 0.02
    fixture.store.update_conversation_statistics.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [401, 500, 503])
async def test_fetch_failure_allows_teardown_and_never_records_free(fixture, status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status))
    ) as client:
        assert not await final_metrics.persist_sandbox_final_metrics(
            'owned-sandbox', fixture.runtime, fixture.context, client
        )
    fixture.store.update_conversation_statistics.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_commit_allows_teardown(fixture):
    fixture.store.update_conversation_statistics.side_effect = RuntimeError(
        'db unavailable'
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=fixture.payload)
        )
    ) as client:
        assert not await final_metrics.persist_sandbox_final_metrics(
            'owned-sandbox', fixture.runtime, fixture.context, client
        )
    assert fixture.persisted[fixture.cid] == 0


@pytest.mark.asyncio
async def test_snapshot_has_a_total_deadline(fixture, monkeypatch):
    monkeypatch.setattr(final_metrics, 'FINAL_METRICS_TIMEOUT_SECONDS', 0.01)

    async def unresponsive_server(_):
        await asyncio.Event().wait()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unresponsive_server)
    ) as client:
        assert not await final_metrics.persist_sandbox_final_metrics(
            'owned-sandbox', fixture.runtime, fixture.context, client
        )
    assert not fixture.db_active


@pytest.mark.asyncio
async def test_unreachable_agent_logs_failure_without_blocking_stop(fixture, caplog):
    async def unreachable(request):
        raise httpx.ConnectError('synthetic unreachable runtime', request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as client:
        assert not await final_metrics.persist_sandbox_final_metrics(
            'owned-sandbox', fixture.runtime, fixture.context, client
        )
    assert 'continuing teardown' in caplog.text
    fixture.store.update_conversation_statistics.assert_not_awaited()
    assert fixture.persisted[fixture.cid] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'failure',
    ['http', 'invalid-json', 'wrong-id', 'invalid-stats', 'invalid-contract', 'save'],
)
async def test_failed_child_does_not_prevent_parent_persistence(fixture, failure):
    child_id = uuid4()
    fixture.persisted[child_id] = 0.02
    fixture.store.search_app_conversation_info.return_value.items.insert(
        0, SimpleNamespace(id=child_id, sandbox_id='owned-sandbox')
    )
    save = fixture.store.update_conversation_statistics.side_effect

    async def update(cid, stats):
        if cid == child_id and failure == 'save':
            raise RuntimeError('child save failed')
        await save(cid, stats)

    fixture.store.update_conversation_statistics.side_effect = update

    async def agent_server(request):
        if request.url.path.endswith(child_id.hex):
            if failure == 'http':
                return httpx.Response(500)
            if failure == 'invalid-json':
                return httpx.Response(200, content=b'not-json')
            payload = {**fixture.payload, 'id': str(child_id)}
            if failure == 'wrong-id':
                payload['id'] = str(uuid4())
            if failure == 'invalid-contract':
                del payload['workspace']
            if failure == 'invalid-stats':
                payload['stats'] = {'usage_to_metrics': 'invalid'}
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=fixture.payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(agent_server)) as client:
        assert not await final_metrics.persist_sandbox_final_metrics(
            'owned-sandbox', fixture.runtime, fixture.context, client
        )
    assert fixture.persisted[fixture.cid] == pytest.approx(0.05)
    assert fixture.persisted[child_id] == 0.02
    assert not fixture.db_active


@pytest.mark.asyncio
async def test_slow_child_cannot_exhaust_parent_snapshot_budget(fixture, monkeypatch):
    child_id = uuid4()
    fixture.store.search_app_conversation_info.return_value.items.insert(
        0, SimpleNamespace(id=child_id, sandbox_id='owned-sandbox')
    )
    monkeypatch.setattr(final_metrics, 'FINAL_METRICS_TIMEOUT_SECONDS', 0.1)
    child_cancelled = asyncio.Event()

    async def agent_server(request):
        if request.url.path.endswith(child_id.hex):
            try:
                await asyncio.Event().wait()
            finally:
                child_cancelled.set()
        return httpx.Response(200, json=fixture.payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(agent_server)) as client:
        assert not await final_metrics.persist_sandbox_final_metrics(
            'owned-sandbox', fixture.runtime, fixture.context, client
        )
    assert fixture.persisted[fixture.cid] == pytest.approx(0.05)
    assert child_cancelled.is_set()
    assert not fixture.db_active
