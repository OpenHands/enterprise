"""Shared utilities for GitLab webhook installation.

This module contains reusable functions and classes for installing GitLab webhooks
that can be used by both the cron job and API routes.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from integrations.gitlab.hook_identity import hook_identity, hook_url, points_at
from integrations.types import GitLabResourceType
from integrations.utils import GITLAB_WEBHOOK_URL
from openhands.app_server.utils.logger import openhands_logger as logger
from storage.gitlab_webhook import GitlabWebhook, WebhookStatus
from storage.gitlab_webhook_store import GitlabWebhookStore

if TYPE_CHECKING:
    from integrations.gitlab.gitlab_service import SaaSGitLabService

# Webhook configuration constants
WEBHOOK_NAME = 'OpenHands Resolver'
SCOPES: list[str] = [
    'note_events',
    'merge_requests_events',
    'confidential_issues_events',
    'issues_events',
    'confidential_note_events',
    'job_events',
    'pipeline_events',
]

# A run refreshes a row's claim when it starts on the row and again when it
# opens the intent just before creating a hook, so the lease only has to
# outlast one row's GitLab calls, not a whole run.
CLAIM_LEASE = timedelta(minutes=10)


class BreakLoopException(Exception):
    """Exception raised when webhook installation conditions are not met or rate limited."""

    pass


async def _delete_row(
    webhook_store: GitlabWebhookStore,
    webhook: GitlabWebhook,
    claim_run_id: UUID,
) -> None:
    if not await webhook_store.delete_claimed(webhook.id, claim_run_id):
        log_stale_write_rejected(webhook, claim_run_id)


async def _update_row(
    webhook_store: GitlabWebhookStore,
    webhook: GitlabWebhook,
    fields: dict[str, Any],
    claim_run_id: UUID,
) -> None:
    if not await webhook_store.update_claimed(webhook.id, claim_run_id, **fields):
        log_stale_write_rejected(webhook, claim_run_id)
        raise BreakLoopException()


def log_stale_write_rejected(webhook: GitlabWebhook, claim_run_id: UUID) -> None:
    logger.warning(
        'gitlab_webhook.stale_write_rejected',
        extra={'webhook_id': webhook.id, 'claim_run_id': str(claim_run_id)},
    )


async def verify_webhook_conditions(
    gitlab_service: SaaSGitLabService,
    resource_type: GitLabResourceType,
    resource_id: str,
    webhook_store: GitlabWebhookStore,
    webhook: GitlabWebhook,
    claim_run_id: UUID,
    check_existing: bool = True,
) -> None:
    """
    Verify all conditions are met for webhook installation.
    Raises BreakLoopException if any condition fails or rate limited.

    Args:
        gitlab_service: GitLab service instance
        resource_type: Type of resource (PROJECT or GROUP)
        resource_id: ID of the resource
        webhook_store: Webhook store instance
        webhook: Webhook object to verify
        claim_run_id: The installer run holding the row's claim; its writes
            apply only while it still holds the claim
        check_existing: Count a hook carrying the row's current identity as
            installed. A reinstall replaces it instead.
    """
    # Check if resource exists
    does_resource_exist, status = await gitlab_service.check_resource_exists(
        resource_type, resource_id
    )

    logger.info(
        'Does resource exists',
        extra={
            'does_resource_exist': does_resource_exist,
            'status': status,
            'resource_id': resource_id,
            'resource_type': resource_type,
        },
    )

    if status == WebhookStatus.RATE_LIMITED:
        raise BreakLoopException()
    if not does_resource_exist and status != WebhookStatus.RATE_LIMITED:
        await _delete_row(webhook_store, webhook, claim_run_id)
        raise BreakLoopException()

    # Check if user has admin access
    (
        is_user_admin_of_resource,
        status,
    ) = await gitlab_service.check_user_has_admin_access_to_resource(
        resource_type, resource_id
    )

    logger.info(
        'Is user admin',
        extra={
            'is_user_admin': is_user_admin_of_resource,
            'status': status,
            'resource_id': resource_id,
            'resource_type': resource_type,
        },
    )

    if status == WebhookStatus.RATE_LIMITED:
        raise BreakLoopException()
    if not is_user_admin_of_resource:
        await _delete_row(webhook_store, webhook, claim_run_id)
        raise BreakLoopException()

    if not check_existing:
        return

    hooks, status = await gitlab_service.list_hooks(resource_type, resource_id)
    if status is not None:
        raise BreakLoopException()
    # A legacy hook at the plain URL doesn't count: its credentials are unknown.
    current_installed = webhook.webhook_uuid is not None and any(
        points_at(hook.get('url'), GITLAB_WEBHOOK_URL)
        and hook_identity(hook['url']) == webhook.webhook_uuid
        for hook in hooks
    )

    logger.info(
        'Does webhook already exist',
        extra={
            'current_installed': current_installed,
            'resource_id': resource_id,
            'resource_type': resource_type,
        },
    )

    if current_installed != webhook.webhook_exists:
        await _update_row(
            webhook_store,
            webhook,
            {'webhook_exists': current_installed},
            claim_run_id,
        )

    if current_installed:
        raise BreakLoopException()


async def install_claimed_webhook(
    gitlab_service: SaaSGitLabService,
    resource_type: GitLabResourceType,
    resource_id: str,
    webhook_store: GitlabWebhookStore,
    webhook: GitlabWebhook,
    run_id: UUID,
) -> str | None:
    """Install or reinstall the row's hook while ``run_id`` holds its claim.

    Each attempt creates its hook under a new intent and identity, and the row
    takes the new credentials only when the intent commits. An attempt that
    can't commit (its claim or intent was lost, or its create failed) abandons
    its intent, so its hook never authenticates and repair deletes it. A
    reinstall deletes every hook with our URL first, then records the request
    generation it served; it never writes reinstall_requested_gen, so a newer
    request survives it.

    Raises BreakLoopException when the row is done for this run.
    """
    # The batch was claimed when the run started; without this, a row reached
    # after the lease could delete hooks that a newer claimant just installed.
    if not await webhook_store.refresh_claim(webhook.id, run_id):
        log_stale_write_rejected(webhook, run_id)
        raise BreakLoopException()

    served_gen = webhook.reinstall_requested_gen
    reinstall = served_gen > webhook.reinstall_done_gen

    await verify_webhook_conditions(
        gitlab_service=gitlab_service,
        resource_type=resource_type,
        resource_id=resource_id,
        webhook_store=webhook_store,
        webhook=webhook,
        claim_run_id=run_id,
        check_existing=not reinstall,
    )

    hook_uuid = str(uuid4())
    log_extra = {
        'webhook_row_id': webhook.id,
        'resource_id': resource_id,
        'resource_type': resource_type,
        'reinstall': reinstall,
        'hook_uuid': hook_uuid,
    }

    if reinstall:
        deleted, status = await gitlab_service.delete_webhooks_with_url(
            resource_type, resource_id, GITLAB_WEBHOOK_URL
        )
        logger.info(
            'gitlab_webhook.reinstall_deleted_hooks',
            extra={**log_extra, 'deleted': deleted, 'status': status},
        )
        if status is not None:
            raise BreakLoopException()

    # Deliveries authenticate against the row's user_id, so it changes to the
    # id the new hook will send only once no other hook is left sending the old one.
    installer_id = gitlab_service.external_auth_id
    if installer_id and webhook.user_id != installer_id:
        await _update_row(webhook_store, webhook, {'user_id': installer_id}, run_id)
        webhook.user_id = installer_id

    if not await webhook_store.open_intent(webhook.id, run_id, hook_uuid):
        log_stale_write_rejected(webhook, run_id)
        raise BreakLoopException()

    webhook_secret = f'{webhook.user_id}-{uuid4()}'
    try:
        webhook_id, status = await gitlab_service.install_webhook(
            resource_type=resource_type,
            resource_id=resource_id,
            webhook_name=WEBHOOK_NAME,
            webhook_url=hook_url(hook_uuid),
            webhook_secret=webhook_secret,
            webhook_uuid=hook_uuid,
            scopes=SCOPES,
        )
    except Exception:
        await webhook_store.abandon_intent(hook_uuid)
        raise
    log_extra.update(webhook_id=webhook_id, status=status)

    if status == WebhookStatus.RATE_LIMITED:
        await webhook_store.abandon_intent(hook_uuid)
        logger.warning('Rate limited while creating webhook', extra=log_extra)
        raise BreakLoopException()
    if not webhook_id:
        # GitLab may still have created it; abandoned, it gets deleted by repair.
        await webhook_store.abandon_intent(hook_uuid)
        logger.error('Failed to create webhook', extra=log_extra)
        return None

    fields: dict[str, Any] = {
        'webhook_secret': webhook_secret,
        'webhook_exists': True,
        'webhook_url': GITLAB_WEBHOOK_URL,
        'scopes': SCOPES,
    }
    if reinstall:
        fields['reinstall_done_gen'] = served_gen
    if not await webhook_store.commit_intent(
        webhook.id, run_id, hook_uuid, webhook.webhook_uuid, **fields
    ):
        await webhook_store.abandon_intent(hook_uuid)
        log_stale_write_rejected(webhook, run_id)
        raise BreakLoopException()
    webhook.webhook_uuid = hook_uuid
    webhook.webhook_secret = webhook_secret
    logger.info('Created new webhook', extra=log_extra)
    return webhook_id
