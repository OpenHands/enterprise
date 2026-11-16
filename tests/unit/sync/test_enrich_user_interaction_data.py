"""enrich_user_interaction_data against a real database.

Only the GitHub collector is replaced; it is the external dependency.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from integrations.types import PRStatus
from openhands.app_server.integrations.service_types import ProviderType
from storage.database import a_session_maker
from storage.openhands_pr import OpenhandsPR

# The job builds its GitHub App client at import; the tests never reach GitHub.
with patch('integrations.github.data_collector.GitHubDataCollector'):
    from sync import enrich_user_interaction_data as job

pytestmark = pytest.mark.usefixtures('app_db_session')


async def _insert_pr(pr_number: int) -> None:
    await job.store.insert_pr(
        OpenhandsPR(
            repo_id='123',
            repo_name='org/repo',
            pr_number=pr_number,
            status=PRStatus.CLOSED,
            provider=ProviderType.GITHUB.value,
            installation_id='42',
            closed_at=datetime(2026, 1, 2),
            created_at=datetime(2026, 1, 1),
        )
    )


async def _attempts(pr_number: int) -> int:
    async with a_session_maker() as session:
        result = await session.execute(
            select(OpenhandsPR.process_attempts).filter(
                OpenhandsPR.pr_number == pr_number
            )
        )
        return result.scalar_one()


async def test_failing_pr_exhausts_retries_across_runs():
    await _insert_pr(1)
    save = AsyncMock(side_effect=RuntimeError('GitHub down'))

    with patch.object(job.data_collector, 'save_full_pr', save):
        for _ in range(job.MAX_RETRIES + 2):
            await job.main()

    assert save.await_count == job.MAX_RETRIES
    assert await _attempts(1) == job.MAX_RETRIES


async def test_attempt_is_counted_before_processing():
    await _insert_pr(1)
    seen: list[int] = []

    async def save(pr: OpenhandsPR) -> None:
        seen.append(await _attempts(pr.pr_number))

    with patch.object(job.data_collector, 'save_full_pr', side_effect=save):
        await job.main()

    assert seen == [1]


async def test_processed_pr_is_not_reprocessed_by_a_stale_scan():
    """An overlapping run holding a stale scan result skips work already done."""
    await _insert_pr(1)
    [stale] = await job.get_unprocessed_prs()
    async with a_session_maker() as session:
        pr = (await session.execute(select(OpenhandsPR))).scalars().one()
        pr.processed = True
        await session.commit()

    save = AsyncMock()
    with patch.object(job.data_collector, 'save_full_pr', save):
        processed = await job.process_pr(stale)

    assert processed is False
    save.assert_not_awaited()
    assert await _attempts(1) == 0


async def test_process_pr_reports_a_successful_claim():
    """process_pr returns True when it claimed the PR."""
    await _insert_pr(1)
    [pr] = await job.get_unprocessed_prs()

    save = AsyncMock()
    with patch.object(job.data_collector, 'save_full_pr', save):
        processed = await job.process_pr(pr)

    assert processed is True
    save.assert_awaited_once()
    assert await _attempts(1) == 1


async def test_processing_uses_the_claimed_row_not_the_stale_scan():
    """insert_pr replaced the row after the scan; enrichment must see the current one."""
    await _insert_pr(1)
    [stale] = await job.get_unprocessed_prs()
    await asyncio.sleep(0.01)
    await _insert_pr(1)
    async with a_session_maker() as session:
        current = (await session.execute(select(OpenhandsPR))).scalars().one()
    assert current.updated_at != stale.updated_at

    save = AsyncMock()
    with patch.object(job.data_collector, 'save_full_pr', save):
        await job.process_pr(stale)

    [saved] = save.await_args.args
    assert saved.updated_at == current.updated_at
