"""OpenhandsPRStore enrichment guards, against a real database.

Enrichment of a closed PR is one-time work, so the contract is "first
successful completion wins": two overlapping runs may both do the GitHub reads,
but only one may commit stats, and the loser must write nothing.
"""

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select

from integrations.types import PRStatus
from openhands.app_server.integrations.service_types import ProviderType
from storage.database import a_session_maker
from storage.openhands_pr import OpenhandsPR
from storage.openhands_pr_store import OpenhandsPRStore

REPO_ID = '123'
PR_NUMBER = 7
MAX_RETRIES = 3

pytestmark = pytest.mark.usefixtures('app_db_session')


@pytest.fixture
def store() -> OpenhandsPRStore:
    return OpenhandsPRStore()


async def _insert_pr(store: OpenhandsPRStore, **overrides) -> None:
    fields = dict(
        repo_id=REPO_ID,
        repo_name='org/repo',
        pr_number=PR_NUMBER,
        status=PRStatus.MERGED,
        provider=ProviderType.GITHUB.value,
        installation_id='42',
        private=False,
        merged=True,
        closed_at=datetime(2026, 1, 2),
        created_at=datetime(2026, 1, 1),
    )
    fields.update(overrides)
    await store.insert_pr(OpenhandsPR(**fields))


async def _load() -> OpenhandsPR:
    async with a_session_maker() as session:
        result = await session.execute(
            select(OpenhandsPR).filter(
                OpenhandsPR.repo_id == REPO_ID, OpenhandsPR.pr_number == PR_NUMBER
            )
        )
        return result.scalars().one()


async def _record(
    store: OpenhandsPRStore, original_updated_at: datetime, commits: int
) -> bool:
    return await store.update_pr_openhands_stats(
        repo_id=REPO_ID,
        pr_number=PR_NUMBER,
        original_updated_at=original_updated_at,
        openhands_helped_author=commits > 0,
        num_openhands_commits=commits,
        num_openhands_review_comments=commits * 10,
        num_openhands_general_comments=commits * 100,
    )


class TestClaimPrForProcessing:
    async def test_claim_consumes_an_attempt_before_work(self, store):
        await _insert_pr(store)

        claimed = await store.claim_pr_for_processing(REPO_ID, PR_NUMBER, MAX_RETRIES)

        assert claimed is not None
        assert claimed.process_attempts == 1
        assert (await _load()).process_attempts == 1

    async def test_claim_refuses_processed_pr(self, store):
        await _insert_pr(store, processed=True)

        assert (
            await store.claim_pr_for_processing(REPO_ID, PR_NUMBER, MAX_RETRIES)
        ) is None
        assert (await _load()).process_attempts == 0

    async def test_crash_loop_exhausts_retries(self, store):
        """Each claim counts even if the run then dies, so retries run out."""
        await _insert_pr(store)

        for _ in range(MAX_RETRIES):
            assert await store.claim_pr_for_processing(REPO_ID, PR_NUMBER, MAX_RETRIES)

        assert (
            await store.claim_pr_for_processing(REPO_ID, PR_NUMBER, MAX_RETRIES)
        ) is None
        assert (await _load()).process_attempts == MAX_RETRIES
        assert await store.get_unprocessed_prs(max_retries=MAX_RETRIES) == []


class TestUpdatePrOpenhandsStats:
    @pytest.mark.parametrize('winner', ['first', 'second'])
    async def test_first_successful_completion_wins(self, store, winner):
        """Two overlapping runs read the same row; whichever commits first wins."""
        await _insert_pr(store)
        run_a = await store.claim_pr_for_processing(REPO_ID, PR_NUMBER, MAX_RETRIES)
        run_b = await store.claim_pr_for_processing(REPO_ID, PR_NUMBER, MAX_RETRIES)
        assert run_a is not None and run_b is not None

        first, second = (run_a, run_b) if winner == 'first' else (run_b, run_a)
        assert await _record(store, first.updated_at, commits=1) is True
        assert await _record(store, second.updated_at, commits=2) is False

        pr = await _load()
        assert pr.processed is True
        assert pr.num_openhands_commits == 1
        assert pr.num_openhands_review_comments == 10
        assert pr.num_openhands_general_comments == 100

    async def test_concurrent_commits_admit_exactly_one(self, store):
        await _insert_pr(store)
        original = (await _load()).updated_at

        results = await asyncio.gather(
            *(_record(store, original, commits=n) for n in (1, 2, 3, 4))
        )

        assert sorted(results) == [False, False, False, True]
        pr = await _load()
        winner = results.index(True) + 1
        assert pr.num_openhands_commits == winner
        assert pr.num_openhands_general_comments == winner * 100

    async def test_refuses_row_replaced_by_insert_pr(self, store):
        """insert_pr replaces the row; a run that read the old row must not write."""
        await _insert_pr(store)
        stale = (await _load()).updated_at
        await asyncio.sleep(0.01)
        await _insert_pr(store, num_commits=99)
        assert (await _load()).updated_at != stale

        assert await _record(store, stale, commits=1) is False
        pr = await _load()
        assert pr.processed is False
        assert pr.num_openhands_commits is None
