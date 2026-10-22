from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import cast
from uuid import uuid4

from sqlalchemy import text

from integrations.gitlab.webhook_installation import (
    BreakLoopException,
    install_claimed_webhook,
)
from openhands.app_server.integrations.gitlab.gitlab_service import GitLabServiceImpl
from openhands.app_server.utils.logger import openhands_logger as logger
from storage.database import a_session_maker
from storage.gitlab_webhook_store import GitlabWebhookStore

CHUNK_SIZE = 100
# Twice the job's 300 s deadline, so a claim is only taken over from a run that
# has been stopped.
CLAIM_LEASE = timedelta(minutes=10)


class VerifyWebhookStatus:
    async def install_webhooks(self):
        """
        Claim rows that need a hook installed or reinstalled, and process them.

        Conditions we check for
            1. Resource exists
                - user could have deleted resource
            2. User has admin access to resource
                - user's permissions to install webhook could have changed
            3. Webhook exists (skipped for a reinstall, which replaces it)
                - user could have removed webhook from resource
                - resource was never setup with webhook

        Every write is conditional on this run's claim, so a run that lost its
        claim to another (after a stall past the lease) changes nothing.
        """

        # Check if the table exists before proceeding
        # This handles cases where the CronJob runs before database migrations complete
        async with a_session_maker() as session:
            query = text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'gitlab_webhook'
                )
            """)
            result = await session.execute(query)
            table_exists = result.scalar() or False

        if not table_exists:
            logger.info(
                'gitlab_webhook table does not exist yet, '
                'waiting for database migrations to complete'
            )
            return

        webhook_store = await GitlabWebhookStore.get_instance()
        run_id = uuid4()
        webhooks_to_process = await webhook_store.claim_rows(
            run_id, CLAIM_LEASE, limit=CHUNK_SIZE
        )

        logger.info(
            'Processing webhook chunks',
            extra={
                'run_id': str(run_id),
                'webhook_ids': [webhook.id for webhook in webhooks_to_process],
            },
        )

        for webhook in webhooks_to_process:
            try:
                resource_type, resource_id = GitlabWebhookStore.determine_resource_type(
                    webhook
                )

                # GitLabServiceImpl returns SaaSGitLabService in enterprise context
                from integrations.gitlab.gitlab_service import SaaSGitLabService

                gitlab_service = cast(
                    SaaSGitLabService,
                    GitLabServiceImpl(external_auth_id=webhook.user_id),
                )

                await install_claimed_webhook(
                    gitlab_service=gitlab_service,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    webhook_store=webhook_store,
                    webhook=webhook,
                    run_id=run_id,
                )
            except BreakLoopException:
                pass
            finally:
                # Release the claim and bump last_synced, success or failure, so
                # the row goes to the back of the queue. A row deleted above, or
                # one another run took over, matches nothing.
                try:
                    await webhook_store.release_claim(webhook.id, run_id)
                except Exception:
                    logger.warning(
                        'Failed to release claim for webhook',
                        extra={'webhook_id': webhook.id},
                        exc_info=True,
                    )


if __name__ == '__main__':
    status_verifier = VerifyWebhookStatus()
    asyncio.run(status_verifier.install_webhooks())
