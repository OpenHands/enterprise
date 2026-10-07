from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, desc, select, update

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.utils.logger import openhands_logger as logger
from storage.database import a_session_maker
from storage.openhands_pr import OpenhandsPR


class OpenhandsPRStore:
    async def insert_pr(self, pr: OpenhandsPR) -> None:
        """
        Insert a new PR or delete and recreate if repo_id and pr_number already exist.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(OpenhandsPR).filter(
                    OpenhandsPR.repo_id == pr.repo_id,
                    OpenhandsPR.pr_number == pr.pr_number,
                    OpenhandsPR.provider == pr.provider,
                )
            )
            existing_pr = result.scalars().first()

            if existing_pr:
                await session.delete(existing_pr)
                await session.flush()

            session.add(pr)
            await session.commit()

    async def claim_pr_for_processing(
        self, repo_id: str, pr_number: int, max_retries: int
    ) -> OpenhandsPR | None:
        """Consume one processing attempt for a PR before any work is done on it.

        Counting the attempt at claim time means a run that crashes mid-way
        still uses it up, so a crash loop cannot retry a PR forever.

        Returns the claimed PR, or None if it is already processed or has no
        attempts left.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                update(OpenhandsPR)
                .where(
                    OpenhandsPR.repo_id == repo_id,
                    OpenhandsPR.pr_number == pr_number,
                    ~OpenhandsPR.processed,
                    OpenhandsPR.process_attempts < max_retries,
                )
                .values(process_attempts=OpenhandsPR.process_attempts + 1)
                .returning(OpenhandsPR)
                .execution_options(synchronize_session=False)
            )
            pr = result.scalars().first()
            await session.commit()
            return pr

    async def update_pr_openhands_stats(
        self,
        repo_id: str,
        pr_number: int,
        original_updated_at: datetime,
        openhands_helped_author: bool,
        num_openhands_commits: int,
        num_openhands_review_comments: int,
        num_openhands_general_comments: int,
    ) -> bool:
        """
        Record OpenHands statistics for a PR, once.

        First successful completion wins: under the row lock the write is
        refused if the PR is already processed, so an overlapping run that
        finishes second writes nothing. The ``updated_at`` check separately
        refuses a row that ``insert_pr`` replaced after this run read it.

        Args:
            repo_id: Repository identifier
            pr_number: Pull request number
            original_updated_at: Original updated_at timestamp to check for concurrent modifications
            openhands_helped_author: Whether OpenHands helped the author (1+ commits)
            num_openhands_commits: Number of commits by OpenHands
            num_openhands_review_comments: Number of review comments by OpenHands
            num_openhands_general_comments: Number of PR comments (not review comments) by OpenHands

        Returns:
            True if this call recorded the stats, False otherwise
        """
        async with a_session_maker() as session:
            # Use row-level locking to prevent concurrent modifications
            result = await session.execute(
                select(OpenhandsPR)
                .filter(
                    OpenhandsPR.repo_id == repo_id, OpenhandsPR.pr_number == pr_number
                )
                .with_for_update()
            )
            pr: OpenhandsPR | None = result.scalars().first()

            if not pr:
                # Current PR snapshot is stale
                logger.warning(f'Did not find PR {pr_number} for repo {repo_id}')
                return False

            if pr.processed or pr.updated_at != original_updated_at:
                await session.rollback()
                return False

            pr.openhands_helped_author = openhands_helped_author
            pr.num_openhands_commits = num_openhands_commits
            pr.num_openhands_review_comments = num_openhands_review_comments
            pr.num_openhands_general_comments = num_openhands_general_comments
            pr.processed = True

            await session.merge(pr)
            await session.commit()
            return True

    async def get_unprocessed_prs(
        self, limit: int = 50, max_retries: int = 3
    ) -> list[OpenhandsPR]:
        """
        Get unprocessed PR entries from the OpenhandsPR table.

        Args:
            limit: Maximum number of PRs to retrieve (default: 50)

        Returns:
            List of OpenhandsPR objects that need processing
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(OpenhandsPR)
                .filter(
                    and_(
                        ~OpenhandsPR.processed,
                        OpenhandsPR.process_attempts < max_retries,
                        OpenhandsPR.provider == ProviderType.GITHUB.value,
                    )
                )
                .order_by(desc(OpenhandsPR.updated_at))
                .limit(limit)
            )
            unprocessed_prs = list(result.scalars().all())

            return unprocessed_prs

    @classmethod
    def get_instance(cls) -> OpenhandsPRStore:
        """Get an instance of the OpenhandsPRStore."""
        return OpenhandsPRStore()
