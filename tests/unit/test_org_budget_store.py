"""Tests for the per-member budget cycle baseline store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from server.constants import ORG_SETTINGS_VERSION
from storage.org import Org
from storage.org_budget_cycle_baseline import OrgBudgetCycleBaseline
from storage.org_budget_store import OrgBudgetStore

CYCLE_A = datetime(2026, 9, 1, tzinfo=UTC)
CYCLE_B = datetime(2026, 10, 1, tzinfo=UTC)
OBSERVED = datetime(2026, 9, 1, 0, 5, tzinfo=UTC)


@pytest.fixture
async def budget_org(async_session_maker):
    org_id = uuid4()
    async with async_session_maker() as session:
        org = Org(
            id=org_id,
            name=f'test-org-{org_id}',
            org_version=ORG_SETTINGS_VERSION,
            enable_proactive_conversation_starters=True,
        )
        session.add(org)
        await session.commit()
    return org


async def _rows(session, org_id) -> list[OrgBudgetCycleBaseline]:
    result = await session.execute(
        select(OrgBudgetCycleBaseline)
        .where(OrgBudgetCycleBaseline.org_id == org_id)
        .order_by(OrgBudgetCycleBaseline.cycle_start_at, OrgBudgetCycleBaseline.user_id)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_record_and_get_are_scoped_to_one_cycle(async_session_maker, budget_org):
    async with async_session_maker() as session:
        store = OrgBudgetStore(session)

        await store.record_cycle_baselines(
            budget_org.id,
            CYCLE_A,
            {'u1': 8.0, 'u2': 3.5},
            source=OrgBudgetCycleBaseline.SOURCE_LIVE_ROLLOVER,
            observed_at=OBSERVED,
        )
        await store.record_cycle_baselines(
            budget_org.id,
            CYCLE_B,
            {'u1': 20.0},
            source=OrgBudgetCycleBaseline.SOURCE_LIVE_ROLLOVER,
            observed_at=OBSERVED + timedelta(days=30),
        )
        await session.commit()

        assert await store.get_cycle_baselines(budget_org.id, CYCLE_A) == {
            'u1': 8.0,
            'u2': 3.5,
        }
        assert await store.get_cycle_baselines(budget_org.id, CYCLE_B) == {'u1': 20.0}
        rows = await _rows(session, budget_org.id)

    assert [(row.cycle_start_at, row.user_id) for row in rows] == [
        (CYCLE_A, 'u1'),
        (CYCLE_A, 'u2'),
        (CYCLE_B, 'u1'),
    ]
    assert {row.source for row in rows} == {'live_rollover'}
    assert rows[0].observed_at == OBSERVED
    assert rows[0].recovery_generation is None


@pytest.mark.asyncio
async def test_record_keeps_the_first_value_unless_replace(
    async_session_maker, budget_org
):
    async with async_session_maker() as session:
        store = OrgBudgetStore(session)

        await store.record_cycle_baselines(
            budget_org.id,
            CYCLE_A,
            {'u1': 8.0},
            source=OrgBudgetCycleBaseline.SOURCE_LIVE_ROLLOVER,
            observed_at=OBSERVED,
        )
        await store.record_cycle_baselines(
            budget_org.id,
            CYCLE_A,
            {'u1': 9.0, 'u2': 1.0},
            source=OrgBudgetCycleBaseline.SOURCE_UPGRADE_RECOVERY,
            observed_at=OBSERVED + timedelta(minutes=1),
        )
        await session.commit()

        assert await store.get_cycle_baselines(budget_org.id, CYCLE_A) == {
            'u1': 8.0,
            'u2': 1.0,
        }
        (first, added) = await _rows(session, budget_org.id)
        assert (first.user_id, first.source) == ('u1', 'live_rollover')
        assert (added.user_id, added.source) == ('u2', 'upgrade_recovery')
        first_id = first.id

        await store.record_cycle_baselines(
            budget_org.id,
            CYCLE_A,
            {'u1': 9.0},
            source=OrgBudgetCycleBaseline.SOURCE_ENABLEMENT,
            observed_at=OBSERVED + timedelta(days=1),
            replace=True,
        )
        await session.commit()
        session.expire_all()

        assert await store.get_cycle_baselines(budget_org.id, CYCLE_A) == {
            'u1': 9.0,
            'u2': 1.0,
        }
        (replaced, _) = await _rows(session, budget_org.id)

    assert replaced.id == first_id
    assert replaced.source == 'enablement'
    assert replaced.observed_at == OBSERVED + timedelta(days=1)


@pytest.mark.asyncio
async def test_record_with_no_baselines_is_a_noop(async_session_maker, budget_org):
    async with async_session_maker() as session:
        store = OrgBudgetStore(session)

        await store.record_cycle_baselines(
            budget_org.id,
            CYCLE_A,
            {},
            source=OrgBudgetCycleBaseline.SOURCE_LIVE_ROLLOVER,
            observed_at=OBSERVED,
        )
        await session.commit()

        assert await store.get_cycle_baselines(budget_org.id, CYCLE_A) == {}
        assert await _rows(session, budget_org.id) == []
