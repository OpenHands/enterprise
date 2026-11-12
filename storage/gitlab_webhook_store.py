from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, asc, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from integrations.types import GitLabResourceType
from openhands.app_server.utils.logger import openhands_logger as logger
from storage.database import a_session_maker
from storage.gitlab_webhook import GitlabWebhook


def _resource_matches(
    resource_type: GitLabResourceType, resource_id: str, provider_id: int | None
):
    """Rows for one GitLab resource of one provider.

    GitLab ids are only unique within an instance, so the provider is part of the
    key. ``provider_id=None`` matches only rows that have no provider.
    """
    column = (
        GitlabWebhook.project_id
        if resource_type == GitLabResourceType.PROJECT
        else GitlabWebhook.group_id
    )
    return and_(
        column == resource_id,
        GitlabWebhook.provider_id.is_not_distinct_from(provider_id),
    )


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
            2. Leverages database-level constraints for uniqueness (a resource id
               is unique per provider)
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
                        stmt = stmt.on_conflict_do_nothing()
                        await session.execute(stmt)

                    # Batch insert for projects
                    if project_values:
                        stmt = insert(GitlabWebhook).values(project_values)
                        stmt = stmt.on_conflict_do_nothing()
                        await session.execute(stmt)

    async def update_webhook(self, webhook: GitlabWebhook, update_fields: dict) -> None:
        """Update a webhook entry based on project_id or group_id.

        Args:
            webhook: GitlabWebhook object containing the updated fields and either project_id or group_id
                     as the identifier, plus its provider_id. Only one of project_id or group_id should be non-null.

        Raises:
            ValueError: If neither project_id nor group_id is provided, or if both are provided.
        """

        resource_type, resource_id = GitlabWebhookStore.determine_resource_type(webhook)
        async with a_session_maker() as session:
            async with session.begin():
                stmt = (
                    update(GitlabWebhook)
                    .where(
                        _resource_matches(
                            resource_type, resource_id, webhook.provider_id
                        )
                    )
                    .values(**update_fields)
                )

                await session.execute(stmt)

    async def delete_webhook(self, webhook: GitlabWebhook) -> None:
        """Delete a webhook entry based on project_id or group_id.

        Args:
            webhook: GitlabWebhook object containing either project_id or group_id
                     as the identifier, plus its provider_id. Only one of project_id or group_id should be non-null.

        Raises:
            ValueError: If neither project_id nor group_id is provided, or if both are provided.
        """

        resource_type, resource_id = GitlabWebhookStore.determine_resource_type(webhook)

        logger.info(
            'Attempting to delete webhook',
            extra={
                'resource_type': resource_type.value,
                'resource_id': resource_id,
                'user_id': getattr(webhook, 'user_id', None),
            },
        )

        async with a_session_maker() as session:
            async with session.begin():
                query = delete(GitlabWebhook).where(
                    _resource_matches(resource_type, resource_id, webhook.provider_id)
                )

                result = await session.execute(query)
                rows_deleted = result.rowcount

                if rows_deleted > 0:
                    logger.info(
                        'Successfully deleted webhook',
                        extra={
                            'resource_type': resource_type.value,
                            'resource_id': resource_id,
                            'rows_deleted': rows_deleted,
                            'user_id': getattr(webhook, 'user_id', None),
                        },
                    )
                else:
                    logger.warning(
                        'No webhook found to delete',
                        extra={
                            'resource_type': resource_type.value,
                            'resource_id': resource_id,
                            'user_id': getattr(webhook, 'user_id', None),
                        },
                    )

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
                            .where(needs_work, claimable)
                            .order_by(asc(GitlabWebhook.last_synced))
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

    async def request_reinstall(
        self,
        resource_type: GitLabResourceType,
        resource_id: str,
        *,
        provider_id: int | None,
    ) -> bool:
        """Ask the installer to delete the resource's hooks and create one.

        Touches nothing but the request counter, so a run holding the row's
        claim finishes undisturbed and the next run serves the request.
        """
        async with a_session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    update(GitlabWebhook)
                    .where(_resource_matches(resource_type, resource_id, provider_id))
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

    async def get_webhook_by_resource_only(
        self,
        resource_type: GitLabResourceType,
        resource_id: str,
        *,
        provider_id: int | None,
    ) -> GitlabWebhook | None:
        """Get a webhook by resource without filtering by user_id.

        This allows any admin user in the organization to manage webhooks,
        not just the original installer.

        Args:
            resource_type: The type of resource (PROJECT or GROUP)
            resource_id: The ID of the resource
            provider_id: The auth provider (GitLab instance) the resource is on

        Returns:
            GitlabWebhook object if found, None otherwise
        """
        async with a_session_maker() as session:
            query = select(GitlabWebhook).where(
                _resource_matches(resource_type, resource_id, provider_id)
            )

            result = await session.execute(query)
            webhook = result.scalars().first()
            return webhook

    async def get_webhooks_by_resources(
        self,
        project_ids: list[str],
        group_ids: list[str],
        *,
        provider_id: int | None,
    ) -> tuple[dict[str, GitlabWebhook], dict[str, GitlabWebhook]]:
        """Bulk fetch webhooks for multiple resources.

        This is more efficient than fetching one at a time in a loop.

        Args:
            project_ids: List of project IDs to fetch
            group_ids: List of group IDs to fetch
            provider_id: The auth provider (GitLab instance) the resources are on

        Returns:
            Tuple of (project_webhook_map, group_webhook_map)
        """
        async with a_session_maker() as session:
            project_webhook_map = {}
            group_webhook_map = {}

            # Fetch all project webhooks in a single query
            if project_ids:
                project_query = select(GitlabWebhook).where(
                    GitlabWebhook.project_id.in_(project_ids),
                    GitlabWebhook.provider_id.is_not_distinct_from(provider_id),
                )
                result = await session.execute(project_query)
                project_webhooks = result.scalars().all()
                project_webhook_map = {wh.project_id: wh for wh in project_webhooks}

            # Fetch all group webhooks in a single query
            if group_ids:
                group_query = select(GitlabWebhook).where(
                    GitlabWebhook.group_id.in_(group_ids),
                    GitlabWebhook.provider_id.is_not_distinct_from(provider_id),
                )
                result = await session.execute(group_query)
                group_webhooks = result.scalars().all()
                group_webhook_map = {wh.group_id: wh for wh in group_webhooks}

            return project_webhook_map, group_webhook_map

    async def reset_webhook_for_reinstallation_by_resource(
        self,
        resource_type: GitLabResourceType,
        resource_id: str,
        updating_user_id: str,
        *,
        provider_id: int | None,
    ) -> bool:
        """Reset webhook for reinstallation without filtering by user_id.

        This allows any admin user to reset webhooks, and updates the user_id
        to track who last modified it.

        Args:
            resource_type: The type of resource (PROJECT or GROUP)
            resource_id: The ID of the resource
            updating_user_id: The user ID performing the update (for audit purposes)
            provider_id: The auth provider (GitLab instance) the resource is on

        Returns:
            True if webhook was reset, False if not found
        """
        async with a_session_maker() as session:
            async with session.begin():
                update_statement = (
                    update(GitlabWebhook)
                    .where(_resource_matches(resource_type, resource_id, provider_id))
                    .values(
                        webhook_exists=False,
                        webhook_uuid=None,
                        user_id=updating_user_id,  # Update to track who modified it
                    )
                )

                result = await session.execute(update_statement)
                rows_updated = result.rowcount

                logger.info(
                    'Reset webhook for reinstallation (organization-wide)',
                    extra={
                        'updating_user_id': updating_user_id,
                        'resource_type': resource_type.value,
                        'resource_id': resource_id,
                        'rows_updated': rows_updated,
                    },
                )

                return rows_updated > 0

    @classmethod
    async def get_instance(cls) -> GitlabWebhookStore:
        """Get an instance of the GitlabWebhookStore.

        Returns:
            An instance of GitlabWebhookStore
        """
        return GitlabWebhookStore()
