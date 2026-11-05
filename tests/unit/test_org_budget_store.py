"""Tests for the org budget store: cycle baselines and threshold edits."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from server.constants import ORG_SETTINGS_VERSION
from server.routes.org_models import OrgBudgetThresholdUpdate
from storage.org import Org
from storage.org_budget_cycle_baseline import OrgBudgetCycleBaseline
from storage.org_budget_store import OrgBudgetStore
from storage.org_budget_threshold import OrgBudgetThreshold

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


def _latched_threshold(org_id, percentage: int, cycle_start) -> OrgBudgetThreshold:
    """A threshold that has already alerted in the cycle starting at cycle_start."""
    return OrgBudgetThreshold(
        org_id=org_id,
        percentage=percentage,
        email_enabled=True,
        slack_enabled=False,
        last_triggered_at=cycle_start,
        last_triggered_cycle_start=cycle_start,
    )


async def _replace_thresholds(session, org_id, updates) -> list[OrgBudgetThreshold]:
    store = OrgBudgetStore(session)
    await store.replace_thresholds(org_id, await store.get_thresholds(org_id), updates)
    await session.commit()
    return await store.get_thresholds(org_id)


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


@pytest.mark.asyncio
async def test_dropping_a_threshold_leaves_the_others_latched(
    async_session_maker, budget_org
):
    cycle_start = datetime.now(UTC)
    async with async_session_maker() as session:
        session.add_all(
            [
                _latched_threshold(budget_org.id, 80, cycle_start),
                _latched_threshold(budget_org.id, 90, cycle_start),
            ]
        )
        await session.commit()

        rows = await _replace_thresholds(
            session,
            budget_org.id,
            [
                OrgBudgetThresholdUpdate(
                    percentage=90, email_enabled=False, slack_enabled=True
                )
            ],
        )

    # Dropping the 80% threshold rewrites neither the settings nor the latch of the
    # 90% one that survived the edit.
    assert [
        (
            row.percentage,
            row.email_enabled,
            row.slack_enabled,
            row.last_triggered_cycle_start,
        )
        for row in rows
    ] == [(90, False, True, cycle_start)]


@pytest.mark.asyncio
async def test_editing_thresholds_leaves_every_surviving_row_latched(
    async_session_maker, budget_org
):
    # An org carries three thresholds by default, so a real edit keeps several
    # latched rows at once: every survivor is updated in place, not just the first.
    cycle_start = datetime.now(UTC)
    async with async_session_maker() as session:
        session.add_all(
            [
                _latched_threshold(budget_org.id, 80, cycle_start),
                _latched_threshold(budget_org.id, 90, cycle_start),
            ]
        )
        await session.commit()

        rows = await _replace_thresholds(
            session,
            budget_org.id,
            [
                OrgBudgetThresholdUpdate(
                    percentage=80, email_enabled=True, slack_enabled=True
                ),
                OrgBudgetThresholdUpdate(
                    percentage=90, email_enabled=True, slack_enabled=True
                ),
            ],
        )

    assert [
        (row.percentage, row.slack_enabled, row.last_triggered_cycle_start)
        for row in rows
    ] == [(80, True, cycle_start), (90, True, cycle_start)]


@pytest.mark.asyncio
async def test_duplicate_rows_for_one_percentage_collapse_onto_the_latched_row(
    async_session_maker, budget_org
):
    # No unique index backs (org_id, percentage), so the table can already hold two
    # rows for one percentage. The edit collapses them onto the latched copy, which
    # is what stops _maybe_send_alerts paging twice in one cycle.
    cycle_start = datetime.now(UTC)
    async with async_session_maker() as session:
        # Insert the unlatched row first so the collapse cannot pass by relying on
        # the latched row being returned first; it must actively prefer the latch.
        session.add_all(
            [
                OrgBudgetThreshold(
                    org_id=budget_org.id,
                    percentage=80,
                    email_enabled=True,
                    slack_enabled=False,
                ),
                _latched_threshold(budget_org.id, 80, cycle_start),
            ]
        )
        await session.commit()

        rows = await _replace_thresholds(
            session,
            budget_org.id,
            [
                OrgBudgetThresholdUpdate(
                    percentage=80, email_enabled=True, slack_enabled=True
                )
            ],
        )

    assert [
        (row.percentage, row.slack_enabled, row.last_triggered_cycle_start)
        for row in rows
    ] == [(80, True, cycle_start)]
