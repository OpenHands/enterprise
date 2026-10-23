from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, asc, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from integrations.types import GitLabResourceType
from storage.database import a_session_maker
from storage.gitlab_hook_intent import GitlabHookIntent, HookIntentState
from storage.gitlab_webhook import GitlabWebhook


def _resource_column(resource_type: GitLabResourceType) -> Any:
    if resource_type == GitLabResourceType.PROJECT:
        return GitlabWebhook.project_id
    return GitlabWebhook.group_id


@dataclass
class GitlabWebhookStore:
    @staticmethod
    def determine_resource_type(
        webhook: GitlabWebhook,
    ) -> tuple[GitLabResourceType, str]:
        if not (webhook.group_id or webhook.project_id):
            raise ValueError('Either project_id or group_id must be provided')

        if webhook.group_id and webhook.project_id:
            raise ValueError('Only one of project_id or group_id should be provided')

        if webhook.group_id:
            return (GitLabResourceType.GROUP, webhook.group_id)
        # At this point, project_id must be set (we checked at least one is set above)
        assert webhook.project_id is not None
        return (GitLabResourceType.PROJECT, webhook.project_id)

    async def store_webhooks(self, project_details: list[GitlabWebhook]) -> None:
        """Store list of project details in db using UPSERT pattern

        Args:
            project_details: List of GitlabWebhook objects to store

        Notes:
            1. Uses UPSERT (INSERT ... ON CONFLICT) to efficiently handle duplicates
            2. Leverages database-level constraints for uniqueness
            3. Performs the operation in a single database transaction
        """
        if not project_details:
            return

        async with a_session_maker() as session:
            async with session.begin():
                # Using __dict__ and filtering out SQLAlchemy internal attributes and 'id'
                values = [
                    {
                        k: v
                        for k, v in webhook.__dict__.items()
                        if not k.startswith('_') and k != 'id'
                    }
                    for webhook in project_details
                ]

                if values:
                    # Separate values into groups and projects
                    group_values = [v for v in values if v.get('group_id')]
                    project_values = [v for v in values if v.get('project_id')]

                    # Batch insert for groups
                    if group_values:
                        stmt = insert(GitlabWebhook).values(group_values)
                        stmt = stmt.on_conflict_do_nothing(index_elements=['group_id'])
                        await session.execute(stmt)

                    # Batch insert for projects
                    if project_values:
                        stmt = insert(GitlabWebhook).values(project_values)
                        stmt = stmt.on_conflict_do_nothing(
                            index_elements=['project_id']
                        )
                        await session.execute(stmt)

    async def claim_rows(
        self, run_id: UUID, lease: timedelta, limit: int = 100
    ) -> list[GitlabWebhook]:
        """Claim up to ``limit`` rows that need a hook installed or reinstalled.

        A row is claimable when unclaimed, or when its claim is older than the
        lease. Rows another run is claiming at the same moment are skipped.
        """
        needs_work = or_(
            GitlabWebhook.webhook_exists.is_(False),
            GitlabWebhook.reinstall_requested_gen > GitlabWebhook.reinstall_done_gen,
        )
        return await self._claim(needs_work, run_id, lease, limit)

    async def claim_resource(
        self,
        resource_type: GitLabResourceType,
        resource_id: str,
        run_id: UUID,
        lease: timedelta,
    ) -> GitlabWebhook | None:
        """Claim the resource's row, or return None while another run holds it."""
        rows = await self._claim(
            _resource_column(resource_type) == resource_id, run_id, lease, limit=1
        )
        return rows[0] if rows else None

    async def claim_rows_for_repair(
        self, run_id: UUID, lease: timedelta, interval: timedelta, limit: int
    ) -> list[GitlabWebhook]:
        """Claim installed rows not swept within ``interval``, oldest sweep first.

        Stamps last_repaired_at as it claims, so a row whose sweep fails waits
        out the interval instead of being retried every run.
        """
        due = and_(
            GitlabWebhook.webhook_exists.is_(True),
            GitlabWebhook.reinstall_requested_gen <= GitlabWebhook.reinstall_done_gen,
            or_(
                GitlabWebhook.last_repaired_at.is_(None),
                GitlabWebhook.last_repaired_at < func.now() - interval,
            ),
        )
        return await self._claim(
            due,
            run_id,
            lease,
            limit,
            order_by=GitlabWebhook.last_repaired_at.asc().nulls_first(),
            last_repaired_at=func.now(),
        )

    async def _claim(
        self,
        where: Any,
        run_id: UUID,
        lease: timedelta,
        limit: int,
        order_by: Any = None,
        **values: Any,
    ) -> list[GitlabWebhook]:
        claimable = or_(
            GitlabWebhook.claimed_at.is_(None),
            GitlabWebhook.claimed_at < func.now() - lease,
        )
        async with a_session_maker() as session:
            async with session.begin():
                rows = list(
                    (
                        await session.execute(
                            select(GitlabWebhook)
                            .where(where, claimable)
                            .order_by(
                                asc(GitlabWebhook.last_synced)
                                if order_by is None
                                else order_by
                            )
                            .limit(limit)
                            .with_for_update(skip_locked=True)
                        )
                    )
                    .scalars()
                    .all()
                )
                for row in rows:
                    row.claim_run_id = run_id
                    row.claimed_at = func.now()
                    for column, value in values.items():
                        setattr(row, column, value)
                await session.flush()
                for row in rows:
                    await session.refresh(row)
        return rows

    async def update_claimed(
        self, webhook_id: int, run_id: UUID, **fields: Any
    ) -> bool:
        """Update a row only while ``run_id`` holds its claim."""
        async with a_session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(GitlabWebhook)
                    .where(
                        GitlabWebhook.id == webhook_id,
                        GitlabWebhook.claim_run_id == run_id,
                    )
                    .values(**fields)
                )
        return result.rowcount > 0

    async def refresh_claim(self, webhook_id: int, run_id: UUID) -> bool:
        return await self.update_claimed(webhook_id, run_id, claimed_at=func.now())

    async def release_claim(self, webhook_id: int, run_id: UUID) -> bool:
        """Mark the row synced and release the claim, if ``run_id`` still holds it."""
        return await self.update_claimed(
            webhook_id,
            run_id,
            claim_run_id=None,
            claimed_at=None,
            last_synced=text('CURRENT_TIMESTAMP'),
        )

    async def delete_claimed(self, webhook_id: int, run_id: UUID) -> bool:
        async with a_session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    delete(GitlabWebhook).where(
                        GitlabWebhook.id == webhook_id,
                        GitlabWebhook.claim_run_id == run_id,
                    )
                )
        return result.rowcount > 0

    async def open_intent(self, webhook_id: int, run_id: UUID, hook_uuid: str) -> bool:
        """Record an open intent for a hook about to be created.

        Committed before the create, so a repair that lists the hook afterwards
        always finds its intent. Refreshes the claim too; records nothing if
        ``run_id`` no longer holds it.
        """
        async with a_session_maker() as session:
            async with session.begin():
                claimed = await session.execute(
                    update(GitlabWebhook)
                    .where(
                        GitlabWebhook.id == webhook_id,
                        GitlabWebhook.claim_run_id == run_id,
                    )
                    .values(claimed_at=func.now())
                )
                if not claimed.rowcount:
                    return False
                session.add(
                    GitlabHookIntent(
                        uuid=hook_uuid,
                        webhook_id=webhook_id,
                        run_id=run_id,
                        state=HookIntentState.OPEN,
                    )
                )
        return True

    async def commit_intent(
        self,
        webhook_id: int,
        run_id: UUID,
        hook_uuid: str,
        replaced_uuid: str | None,
        **fields: Any,
    ) -> bool:
        """Make the intent's hook the row's current identity.

        Applies only while ``run_id`` holds the claim, the intent is still open
        and the row's uuid is still ``replaced_uuid``. The previous identity
        becomes replaced in the same transaction.
        """
        async with a_session_maker() as session:
            async with session.begin():
                state = await session.scalar(
                    select(GitlabHookIntent.state)
                    .where(
                        GitlabHookIntent.uuid == hook_uuid,
                        GitlabHookIntent.webhook_id == webhook_id,
                    )
                    .with_for_update()
                )
                if state != HookIntentState.OPEN:
                    return False
                updated = await session.execute(
                    update(GitlabWebhook)
                    .where(
                        GitlabWebhook.id == webhook_id,
                        GitlabWebhook.claim_run_id == run_id,
                        GitlabWebhook.webhook_uuid.is_not_distinct_from(replaced_uuid),
                    )
                    .values(webhook_uuid=hook_uuid, **fields)
                )
                if not updated.rowcount:
                    return False
                await session.execute(
                    update(GitlabHookIntent)
                    .where(
                        GitlabHookIntent.webhook_id == webhook_id,
                        GitlabHookIntent.state == HookIntentState.COMMITTED,
                    )
                    .values(state=HookIntentState.REPLACED)
                )
                await session.execute(
                    update(GitlabHookIntent)
                    .where(GitlabHookIntent.uuid == hook_uuid)
                    .values(state=HookIntentState.COMMITTED)
                )
        return True

    async def abandon_intent(self, hook_uuid: str) -> bool:
        """Mark an open intent as never becoming current."""
        async with a_session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(GitlabHookIntent)
                    .where(
                        GitlabHookIntent.uuid == hook_uuid,
                        GitlabHookIntent.state == HookIntentState.OPEN,
                    )
                    .values(state=HookIntentState.ABANDONED)
                )
        return result.rowcount > 0

    async def abandon_open_intents(self, webhook_id: int, run_id: UUID) -> int:
        """Abandon other runs' open intents on a row ``run_id`` holds.

        Committing needs the claim, so no other run's open intent can commit.
        """
        holds_claim = (
            select(GitlabWebhook.id)
            .where(
                GitlabWebhook.id == webhook_id,
                GitlabWebhook.claim_run_id == run_id,
            )
            .exists()
        )
        async with a_session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(GitlabHookIntent)
                    .where(
                        GitlabHookIntent.webhook_id == webhook_id,
                        GitlabHookIntent.state == HookIntentState.OPEN,
                        GitlabHookIntent.run_id.is_distinct_from(run_id),
                        holds_claim,
                    )
                    .values(state=HookIntentState.ABANDONED)
                )
        return result.rowcount

    async def get_intent_states(self, webhook_id: int) -> dict[str, str]:
        async with a_session_maker() as session:
            rows = await session.execute(
                select(GitlabHookIntent.uuid, GitlabHookIntent.state).where(
                    GitlabHookIntent.webhook_id == webhook_id
                )
            )
            return {uuid: state for uuid, state in rows}

    async def request_reinstall(
        self, resource_type: GitLabResourceType, resource_id: str
    ) -> bool:
        """Ask the installer to delete the resource's hooks and create one.

        Touches nothing but the request counter, so a run holding the row's
        claim finishes undisturbed and the next run serves the request.
        """
        async with a_session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(GitlabWebhook)
                    .where(_resource_column(resource_type) == resource_id)
                    .values(
                        reinstall_requested_gen=GitlabWebhook.reinstall_requested_gen
                        + 1
                    )
                )
        return result.rowcount > 0

    async def get_webhook_secret(self, webhook_uuid: str, user_id: str) -> str | None:
        """
        Gets webhook secret given the webhook uuid and admin keycloak user id
        """
        async with a_session_maker() as session:
            query = (
                select(GitlabWebhook)
                .where(
                    and_(
                        GitlabWebhook.user_id == user_id,
                        GitlabWebhook.webhook_uuid == webhook_uuid,
                    )
                )
                .limit(1)
            )

            result = await session.execute(query)
            webhooks: list[GitlabWebhook] = list(result.scalars().all())

            if len(webhooks):
                return webhooks[0].webhook_secret
            return None

    async def get_webhooks_by_resources(
        self, project_ids: list[str], group_ids: list[str]
    ) -> tuple[dict[str, GitlabWebhook], dict[str, GitlabWebhook]]:
        """Bulk fetch webhooks for multiple resources.

        This is more efficient than fetching one at a time in a loop.

        Args:
            project_ids: List of project IDs to fetch
            group_ids: List of group IDs to fetch

        Returns:
            Tuple of (project_webhook_map, group_webhook_map)
        """
        async with a_session_maker() as session:
            project_webhook_map = {}
            group_webhook_map = {}

            # Fetch all project webhooks in a single query
            if project_ids:
                project_query = select(GitlabWebhook).where(
                    GitlabWebhook.project_id.in_(project_ids)
                )
                result = await session.execute(project_query)
                project_webhooks = result.scalars().all()
                project_webhook_map = {wh.project_id: wh for wh in project_webhooks}

            # Fetch all group webhooks in a single query
            if group_ids:
                group_query = select(GitlabWebhook).where(
                    GitlabWebhook.group_id.in_(group_ids)
                )
                result = await session.execute(group_query)
                group_webhooks = result.scalars().all()
                group_webhook_map = {wh.group_id: wh for wh in group_webhooks}

            return project_webhook_map, group_webhook_map

    @classmethod
    async def get_instance(cls) -> GitlabWebhookStore:
        """Get an instance of the GitlabWebhookStore.

        Returns:
            An instance of GitlabWebhookStore
        """
        return GitlabWebhookStore()
