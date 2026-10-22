"""Shared utilities for GitLab webhook installation.

This module contains reusable functions and classes for installing GitLab webhooks
that can be used by both the cron job and API routes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

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


class BreakLoopException(Exception):
    """Exception raised when webhook installation conditions are not met or rate limited."""

    pass


async def _delete_row(
    webhook_store: GitlabWebhookStore,
    webhook: GitlabWebhook,
    claim_run_id: UUID | None,
) -> None:
    if claim_run_id is None:
        await webhook_store.delete_webhook(webhook)
    elif not await webhook_store.delete_claimed(webhook.id, claim_run_id):
        log_stale_write_rejected(webhook, claim_run_id)


async def _update_row(
    webhook_store: GitlabWebhookStore,
    webhook: GitlabWebhook,
    fields: dict[str, Any],
    claim_run_id: UUID | None,
) -> None:
    if claim_run_id is None:
        await webhook_store.update_webhook(webhook, fields)
    elif not await webhook_store.update_claimed(webhook.id, claim_run_id, **fields):
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
    claim_run_id: UUID | None = None,
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
        check_existing: Adopt a hook already on the resource. A reinstall
            replaces it instead.
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

    # Check if webhook already exists
    (
        does_webhook_exist_on_resource,
        status,
    ) = await gitlab_service.check_webhook_exists_on_resource(
        resource_type=resource_type,
        resource_id=resource_id,
        webhook_url=GITLAB_WEBHOOK_URL,
    )

    logger.info(
        'Does webhook already exist',
        extra={
            'does_webhook_exist_on_resource': does_webhook_exist_on_resource,
            'status': status,
            'resource_id': resource_id,
            'resource_type': resource_type,
        },
    )

    if status == WebhookStatus.RATE_LIMITED:
        raise BreakLoopException()
    if does_webhook_exist_on_resource != webhook.webhook_exists:
        await _update_row(
            webhook_store,
            webhook,
            {'webhook_exists': does_webhook_exist_on_resource},
            claim_run_id,
        )

    if does_webhook_exist_on_resource:
        raise BreakLoopException()


async def install_webhook_on_resource(
    gitlab_service: SaaSGitLabService,
    resource_type: GitLabResourceType,
    resource_id: str,
    webhook_store: GitlabWebhookStore,
    webhook: GitlabWebhook,
) -> tuple[str | None, WebhookStatus | None]:
    """
    Install webhook on a GitLab resource.

    Args:
        gitlab_service: GitLab service instance
        resource_type: Type of resource (PROJECT or GROUP)
        resource_id: ID of the resource
        webhook_store: Webhook store instance
        webhook: Webhook object to install

    Returns:
        Tuple of (webhook_id, status)
    """
    webhook_secret = f'{webhook.user_id}-{str(uuid4())}'
    webhook_uuid = f'{str(uuid4())}'

    webhook_id, status = await gitlab_service.install_webhook(
        resource_type=resource_type,
        resource_id=resource_id,
        webhook_name=WEBHOOK_NAME,
        webhook_url=GITLAB_WEBHOOK_URL,
        webhook_secret=webhook_secret,
        webhook_uuid=webhook_uuid,
        scopes=SCOPES,
    )

    log_extra = {
        'webhook_id': webhook_id,
        'status': status,
        'resource_id': resource_id,
        'resource_type': resource_type,
    }

    if status == WebhookStatus.RATE_LIMITED:
        logger.warning('Rate limited while creating webhook', extra=log_extra)
        raise BreakLoopException()

    if webhook_id:
        await webhook_store.update_webhook(
            webhook=webhook,
            update_fields={
                'webhook_secret': webhook_secret,
                'webhook_exists': True,  # webhook was created
                'webhook_url': GITLAB_WEBHOOK_URL,
                'scopes': SCOPES,
                'webhook_uuid': webhook_uuid,  # required to identify which webhook installation is sending payload
            },
        )
        logger.info('Created new webhook', extra=log_extra)
    else:
        logger.error('Failed to create webhook', extra=log_extra)

    return webhook_id, status


async def install_claimed_webhook(
    gitlab_service: SaaSGitLabService,
    resource_type: GitLabResourceType,
    resource_id: str,
    webhook_store: GitlabWebhookStore,
    webhook: GitlabWebhook,
    run_id: UUID,
) -> str | None:
    """Install or reinstall the row's hook while ``run_id`` holds its claim.

    The row keeps one uuid and secret across attempts, written before any
    create, so a hook whose create response was lost still authenticates and
    the next run adopts it. A reinstall deletes every hook with our URL and
    creates one, then records the request generation it served; it never
    writes reinstall_requested_gen, so a newer request survives it.

    Raises BreakLoopException when the row is done for this run.
    """
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

    log_extra = {
        'webhook_row_id': webhook.id,
        'resource_id': resource_id,
        'resource_type': resource_type,
        'reinstall': reinstall,
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

    if not (webhook.webhook_uuid and webhook.webhook_secret):
        credentials = {
            'webhook_secret': f'{webhook.user_id}-{uuid4()}',
            'webhook_uuid': str(uuid4()),
        }
        await _update_row(webhook_store, webhook, credentials, run_id)
        webhook.webhook_secret = credentials['webhook_secret']
        webhook.webhook_uuid = credentials['webhook_uuid']

    if not await webhook_store.refresh_claim(webhook.id, run_id):
        log_stale_write_rejected(webhook, run_id)
        raise BreakLoopException()

    webhook_id, status = await gitlab_service.install_webhook(
        resource_type=resource_type,
        resource_id=resource_id,
        webhook_name=WEBHOOK_NAME,
        webhook_url=GITLAB_WEBHOOK_URL,
        webhook_secret=webhook.webhook_secret,
        webhook_uuid=webhook.webhook_uuid,
        scopes=SCOPES,
    )
    log_extra.update(webhook_id=webhook_id, status=status)

    if status == WebhookStatus.RATE_LIMITED:
        logger.warning('Rate limited while creating webhook', extra=log_extra)
        raise BreakLoopException()
    if not webhook_id:
        logger.error('Failed to create webhook', extra=log_extra)
        return None

    fields: dict[str, Any] = {
        'webhook_exists': True,
        'webhook_url': GITLAB_WEBHOOK_URL,
        'scopes': SCOPES,
    }
    if reinstall:
        fields['reinstall_done_gen'] = served_gen
    await _update_row(webhook_store, webhook, fields, run_id)
    logger.info('Created new webhook', extra=log_extra)
    return webhook_id
