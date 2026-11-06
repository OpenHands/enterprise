"""Known spend survives teardown without relying on asynchronous refresh."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from openhands.app_server import config
from openhands.app_server.errors import SandboxDeleteRetryError
from openhands.app_server.sandbox import final_metrics
from openhands.sdk import ConversationStats
from openhands.sdk.llm.utils.metrics import Metrics


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
        payload={'id': str(conversation_id), 'stats': stats.model_dump(mode='json')},
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
async def test_fetch_failure_is_retryable_and_never_records_free(fixture, status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status))
    ) as client:
        with pytest.raises(SandboxDeleteRetryError):
            await final_metrics.persist_sandbox_final_metrics(
                'owned-sandbox', fixture.runtime, fixture.context, client
            )
    fixture.store.update_conversation_statistics.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_sandbox_is_not_fetched_or_written(fixture):
    fixture.info.sandbox_id = 'someone-else'
    requests = []

    def unexpected_request(request):
        requests.append(request)
        return httpx.Response(500)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected_request)
    ) as client:
        with pytest.raises(SandboxDeleteRetryError):
            await final_metrics.persist_sandbox_final_metrics(
                'owned-sandbox', fixture.runtime, fixture.context, client
            )
    assert not requests
    fixture.store.update_conversation_statistics.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_commit_blocks_teardown(fixture):
    fixture.store.update_conversation_statistics.side_effect = RuntimeError(
        'db unavailable'
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=fixture.payload)
        )
    ) as client:
        with pytest.raises(SandboxDeleteRetryError):
            await final_metrics.persist_sandbox_final_metrics(
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
        with pytest.raises(SandboxDeleteRetryError):
            await final_metrics.persist_sandbox_final_metrics(
                'owned-sandbox', fixture.runtime, fixture.context, client
            )
    assert not fixture.db_active
