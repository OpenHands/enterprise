"""Query-level tests for the model-usage aggregation in org usage stats."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from server.services.org_conversation_service import OrgConversationService
from storage.stored_conversation_cost_event import StoredConversationCostEvent
from storage.stored_conversation_metadata import StoredConversationMetadata
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas

ORG_ID = uuid4()
USER_ID = uuid4()


@pytest.fixture(autouse=True)
def seed_org_and_user(create_org, create_user):
    """``conversation_metadata_saas`` has foreign keys to both org and user."""
    create_org(id=ORG_ID)
    create_user(id=USER_ID, current_org_id=ORG_ID)


async def _conversation(
    session,
    conversation_id,
    llm_model,
    cost,
    prompt,
    completion,
    *,
    created_at=None,
    agent_kind=None,
    user_id=USER_ID,
    last_updated_at=None,
    parent_conversation_id=None,
):
    session.add(
        StoredConversationMetadata(
            conversation_id=conversation_id,
            conversation_version='V1',
            llm_model=llm_model,
            accumulated_cost=cost,
            prompt_tokens=prompt,
            completion_tokens=completion,
            created_at=created_at or datetime.now(UTC) - timedelta(days=1),
            agent_kind=agent_kind,
            last_updated_at=last_updated_at,
            parent_conversation_id=parent_conversation_id,
        )
    )
    session.add(
        StoredConversationMetadataSaas(
            conversation_id=conversation_id,
            user_id=user_id,
            org_id=ORG_ID,
        )
    )
    # Cost events reference conversation_metadata, and SQLAlchemy has no
    # relationship to order the inserts for us.
    await session.flush()


@pytest.mark.asyncio
async def test_model_usage_ledger_legacy_and_no_event_rows(async_session_maker):
    occurred = datetime.now(UTC) - timedelta(hours=2)
    async with async_session_maker() as session:
        # A: attributed ledger rows across two models; the conversation's own
        # llm_model label must NOT override per-event attribution.
        await _conversation(
            session, 'conv-a', 'litellm_proxy/current-label', 0.25, 300, 30
        )
        session.add(
            StoredConversationCostEvent(
                conversation_id='conv-a',
                cost_delta=0.08,
                occurred_at=occurred,
                usage_id='agent',
                llm_model='litellm_proxy/gpt-5.5',
                prompt_tokens=100,
                completion_tokens=10,
            )
        )
        session.add(
            StoredConversationCostEvent(
                conversation_id='conv-a',
                cost_delta=0.17,
                occurred_at=occurred,
                usage_id='profile:opus:x1',
                llm_model='litellm_proxy/claude-opus-4-8',
                prompt_tokens=200,
                completion_tokens=20,
            )
        )
        # B: pre-migration NULL rows fall back to the conversation label;
        # NULL token fields contribute zero tokens.
        await _conversation(session, 'conv-b', 'legacy-model', 0.30, 999, 99)
        session.add(
            StoredConversationCostEvent(
                conversation_id='conv-b',
                cost_delta=0.30,
                occurred_at=occurred,
            )
        )
        # C: no ledger rows at all — kept via the legacy aggregation.
        await _conversation(session, 'conv-c', 'old-model', 0.55, 50, 5)
        await session.commit()

    async with async_session_maker() as session:
        service = OrgConversationService(db_session=session)
        base_filter = [
            StoredConversationMetadata.conversation_version == 'V1',
            StoredConversationMetadataSaas.org_id == ORG_ID,
        ]
        cutoff = datetime.now(UTC) - timedelta(days=30)
        model_usage = await service._get_model_usage(base_filter, cutoff)
        agent_usage = await service._get_agent_usage(base_filter, cutoff)

    by_model = {m.model_name: m for m in model_usage}
    assert by_model['litellm_proxy/gpt-5.5'].total_cost == pytest.approx(0.08)
    assert by_model['litellm_proxy/gpt-5.5'].total_tokens == 110
    assert by_model['litellm_proxy/claude-opus-4-8'].total_cost == pytest.approx(0.17)
    assert by_model['litellm_proxy/claude-opus-4-8'].total_tokens == 220
    assert by_model['legacy-model'].total_cost == pytest.approx(0.30)
    assert by_model['legacy-model'].total_tokens == 0
    assert by_model['old-model'].total_cost == pytest.approx(0.55)
    assert by_model['old-model'].total_tokens == 55
    # The relabel-prone conversation label never appears as its own row.
    assert 'litellm_proxy/current-label' not in by_model
    # Ordered by spend, descending.
    costs = [m.total_cost for m in model_usage]
    assert costs == sorted(costs, reverse=True)
    assert agent_usage['OpenHands'][0] == 3
    assert agent_usage['OpenHands'][1] == pytest.approx(1.10)


@pytest.mark.asyncio
async def test_agent_usage_groups_acp_models_and_deduplicates_conversations(
    async_session_maker,
):
    occurred = datetime.now(UTC) - timedelta(hours=2)
    async with async_session_maker() as session:
        await _conversation(
            session,
            'acp-openai',
            'gpt-current',
            0.30,
            30,
            3,
            agent_kind='acp',
        )
        session.add_all(
            [
                StoredConversationCostEvent(
                    conversation_id='acp-openai',
                    cost_delta=0.10,
                    occurred_at=occurred,
                    llm_model='openai/gpt-5',
                ),
                StoredConversationCostEvent(
                    conversation_id='acp-openai',
                    cost_delta=0.20,
                    occurred_at=occurred,
                    llm_model='gpt-4.1',
                ),
            ]
        )
        await _conversation(
            session,
            'acp-claude',
            'claude-current',
            0.40,
            40,
            4,
            agent_kind='acp',
        )
        session.add(
            StoredConversationCostEvent(
                conversation_id='acp-claude',
                cost_delta=0.40,
                occurred_at=occurred,
                llm_model='claude-sonnet',
            )
        )
        await _conversation(
            session,
            'acp-codex-legacy',
            'codex-mini',
            0.50,
            50,
            5,
            agent_kind='acp',
        )
        await session.commit()

    async with async_session_maker() as session:
        service = OrgConversationService(db_session=session)
        agent_usage = await service._get_agent_usage(
            [
                StoredConversationMetadata.conversation_version == 'V1',
                StoredConversationMetadataSaas.org_id == ORG_ID,
            ],
            datetime.now(UTC) - timedelta(days=30),
        )

    assert set(agent_usage) == {'OpenAI', 'Claude', 'Codex'}
    assert agent_usage['OpenAI'][0] == 1
    assert agent_usage['OpenAI'][1] == pytest.approx(0.30)
    assert agent_usage['Claude'][0] == 1
    assert agent_usage['Claude'][1] == pytest.approx(0.40)
    assert agent_usage['Codex'][0] == 1
    assert agent_usage['Codex'][1] == pytest.approx(0.50)


async def _seed_spend_time_boundary_scenario(async_session_maker):
    now = datetime.now(UTC)
    recent = now - timedelta(hours=2)
    old = now - timedelta(days=45)
    async with async_session_maker() as session:
        await _conversation(
            session,
            'old-active-a',
            'litellm_proxy/current-label',
            0.40,
            100,
            10,
            created_at=old,
        )
        session.add(
            StoredConversationCostEvent(
                conversation_id='old-active-a',
                cost_delta=0.40,
                occurred_at=recent,
                usage_id='agent',
                llm_model='litellm_proxy/gpt-5.5',
                prompt_tokens=100,
                completion_tokens=10,
            )
        )
        await _conversation(
            session,
            'old-active-b',
            'litellm_proxy/current-label',
            0.10,
            20,
            2,
            created_at=old,
        )
        session.add(
            StoredConversationCostEvent(
                conversation_id='old-active-b',
                cost_delta=0.10,
                occurred_at=recent,
                usage_id='agent',
                llm_model='litellm_proxy/gpt-5.5',
                prompt_tokens=20,
                completion_tokens=2,
            )
        )
        await _conversation(
            session,
            'recent-stale',
            'stale-model',
            0.60,
            600,
            60,
            created_at=now - timedelta(days=1),
        )
        session.add(
            StoredConversationCostEvent(
                conversation_id='recent-stale',
                cost_delta=0.60,
                occurred_at=old,
                usage_id='agent',
                llm_model='stale-model',
                prompt_tokens=600,
                completion_tokens=60,
            )
        )
        await _conversation(
            session,
            'recent-no-ledger',
            'legacy-model',
            0.25,
            20,
            2,
            created_at=now - timedelta(days=1),
        )
        await _conversation(
            session,
            'old-no-ledger',
            'old-model',
            0.90,
            900,
            90,
            created_at=old,
        )
        await session.commit()
    return now


@pytest.mark.asyncio
async def test_usage_stats_follow_spend_time_across_window_boundaries(
    async_session_maker,
):
    """Spend metrics include recent usage, independent of conversation age."""
    seeded_at = await _seed_spend_time_boundary_scenario(async_session_maker)

    async with async_session_maker() as session:
        service = OrgConversationService(db_session=session)
        stats = await service.get_usage_stats(ORG_ID, days=30)

    assert stats.agent_runs == 2
    assert stats.usage_conversation_count == 3
    assert stats.active_users == 1
    assert stats.estimated_spend == pytest.approx(0.75)
    assert stats.total_tokens == 154

    assert len(stats.team_usage) == 1
    assert stats.team_usage[0].conversation_count == 3
    assert stats.team_usage[0].total_tokens == 154

    assert len(stats.agent_usage) == 1
    assert stats.agent_usage[0].agent_name == 'OpenHands'
    assert stats.agent_usage[0].conversation_count == 3
    assert stats.agent_usage[0].total_cost == pytest.approx(0.75)

    assert sum(row.conversations for row in stats.daily_usage) == 2
    assert sum(row.tokens for row in stats.daily_usage) == 154
    daily_tokens = {row.date: row.tokens for row in stats.daily_usage}
    expected_daily: dict[str, int] = {}
    recent_day = (seeded_at - timedelta(hours=2)).strftime('%Y-%m-%d')
    legacy_day = (seeded_at - timedelta(days=1)).strftime('%Y-%m-%d')
    expected_daily[recent_day] = expected_daily.get(recent_day, 0) + 132
    expected_daily[legacy_day] = expected_daily.get(legacy_day, 0) + 22
    for day, tokens in expected_daily.items():
        assert daily_tokens[day] == tokens

    assert sum(row.total_cost for row in stats.model_usage) == pytest.approx(0.75)
    assert sum(row.total_tokens for row in stats.model_usage) == 154
    assert sum(row.conversation_count for row in stats.model_usage) == 3


def _cost_event(conversation_id, cost, occurred_at, llm_model='model-a'):
    return StoredConversationCostEvent(
        conversation_id=conversation_id,
        cost_delta=cost,
        occurred_at=occurred_at,
        llm_model=llm_model,
    )


@pytest.mark.asyncio
async def test_my_usage_counts_only_the_callers_spend(async_session_maker, create_user):
    # Arrange
    teammate_id = uuid4()
    create_user(id=teammate_id, current_org_id=ORG_ID)
    now = datetime.now(UTC)
    async with async_session_maker() as session:
        await _conversation(session, 'mine', 'label', 3.0, 0, 0)
        await _conversation(
            session, 'theirs', 'label', 500.0, 0, 0, user_id=teammate_id
        )
        session.add_all(
            [
                _cost_event('mine', 2.0, now - timedelta(hours=1), 'model-a'),
                _cost_event('mine', 1.0, now - timedelta(days=1), 'model-b'),
                _cost_event('theirs', 500.0, now - timedelta(hours=1), 'model-c'),
            ]
        )
        await session.commit()

    # Act
    async with async_session_maker() as session:
        stats = await OrgConversationService(db_session=session).get_my_usage_stats(
            org_id=ORG_ID, user_id=USER_ID, days=7
        )

    # Assert
    assert stats.total_spend == pytest.approx(3.0)
    assert {row.model_name: row.total_cost for row in stats.model_usage} == {
        'model-a': pytest.approx(2.0),
        'model-b': pytest.approx(1.0),
    }


@pytest.mark.asyncio
async def test_my_usage_reports_one_entry_per_day_ending_today(async_session_maker):
    # Arrange
    now = datetime.now(UTC)
    async with async_session_maker() as session:
        await _conversation(session, 'mine', 'label', 2.0, 0, 0)
        session.add(_cost_event('mine', 2.0, now - timedelta(days=2)))
        await session.commit()

    # Act
    async with async_session_maker() as session:
        stats = await OrgConversationService(db_session=session).get_my_usage_stats(
            org_id=ORG_ID, user_id=USER_ID, days=7
        )

    # Assert
    daily = {row.date: row.cost for row in stats.daily_spend}
    assert [row.date for row in stats.daily_spend] == [
        (now - timedelta(days=offset)).strftime('%Y-%m-%d')
        for offset in range(6, -1, -1)
    ]
    assert daily[(now - timedelta(days=2)).strftime('%Y-%m-%d')] == pytest.approx(2.0)
    assert daily[now.strftime('%Y-%m-%d')] == 0


@pytest.mark.asyncio
async def test_my_usage_compares_against_the_equally_long_previous_period(
    async_session_maker,
):
    # Arrange
    now = datetime.now(UTC)
    async with async_session_maker() as session:
        await _conversation(
            session, 'mine', 'label', 5.0, 0, 0, created_at=now - timedelta(days=20)
        )
        session.add_all(
            [
                _cost_event('mine', 1.0, now - timedelta(days=1)),
                _cost_event('mine', 4.0, now - timedelta(days=9)),
            ]
        )
        await session.commit()

    # Act
    async with async_session_maker() as session:
        stats = await OrgConversationService(db_session=session).get_my_usage_stats(
            org_id=ORG_ID, user_id=USER_ID, days=7
        )

    # Assert
    assert stats.total_spend == pytest.approx(1.0)
    assert stats.previous_period_spend == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_my_usage_counts_conversations_that_predate_the_cost_ledger(
    async_session_maker,
):
    # Arrange: a cost but no ledger rows, as for conversations older than the ledger
    async with async_session_maker() as session:
        await _conversation(session, 'legacy', 'old-model', 0.55, 0, 0)
        await session.commit()

    # Act
    async with async_session_maker() as session:
        stats = await OrgConversationService(db_session=session).get_my_usage_stats(
            org_id=ORG_ID, user_id=USER_ID, days=7
        )

    # Assert
    assert stats.total_spend == pytest.approx(0.55)


@pytest.mark.asyncio
async def test_my_usage_lists_own_six_most_recent_top_level_conversations(
    async_session_maker, create_user
):
    # Arrange
    teammate_id = uuid4()
    create_user(id=teammate_id, current_org_id=ORG_ID)
    now = datetime.now(UTC)
    async with async_session_maker() as session:
        for age in range(7):
            await _conversation(
                session,
                f'mine-{age}',
                'label',
                float(age),
                0,
                0,
                last_updated_at=now - timedelta(hours=age + 1),
            )
        await _conversation(
            session,
            'mine-sub',
            'label',
            1.0,
            0,
            0,
            last_updated_at=now,
            parent_conversation_id='mine-0',
        )
        await _conversation(
            session,
            'theirs',
            'label',
            9.0,
            0,
            0,
            user_id=teammate_id,
            last_updated_at=now,
        )
        await session.commit()

    # Act
    async with async_session_maker() as session:
        stats = await OrgConversationService(db_session=session).get_my_usage_stats(
            org_id=ORG_ID, user_id=USER_ID, days=7
        )

    # Assert
    assert [row.conversation_id for row in stats.recent_usage] == [
        f'mine-{age}' for age in range(6)
    ]
    assert stats.recent_usage[1].accumulated_cost == pytest.approx(1.0)
