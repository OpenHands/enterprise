"""Delete our GitLab hooks that can never authenticate, and convert legacy hooks.

A delivery authenticates only when its hook's uuid is the row's current
``webhook_uuid``, and only a committed intent sets that. So on a row this run
has claimed, every hook with our URL whose identity isn't the row's current
uuid is stale. A legacy hook (plain URL, no identity) may still be the one that
authenticates, but GitLab won't say which, so the resource gets an identity
hook first and the legacy hooks are deleted after it commits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from integrations.gitlab.hook_identity import hook_identity, points_at
from integrations.gitlab.webhook_installation import (
    BreakLoopException,
    install_claimed_webhook,
)
from integrations.types import GitLabResourceType
from integrations.utils import GITLAB_WEBHOOK_URL
from openhands.app_server.utils.logger import openhands_logger as logger
from storage.gitlab_webhook import GitlabWebhook
from storage.gitlab_webhook_store import GitlabWebhookStore

if TYPE_CHECKING:
    from integrations.gitlab.gitlab_service import SaaSGitLabService


async def repair_claimed_hooks(
    gitlab_service: SaaSGitLabService,
    resource_type: GitLabResourceType,
    resource_id: str,
    webhook_store: GitlabWebhookStore,
    webhook: GitlabWebhook,
    run_id: UUID,
) -> None:
    """Sweep one claimed row's resource. Raises BreakLoopException to stop early."""
    abandoned = await webhook_store.abandon_open_intents(webhook.id, run_id)

    hooks, status = await gitlab_service.list_hooks(resource_type, resource_id)
    if status is not None:
        raise BreakLoopException()

    current: list[dict] = []
    stale: list[dict] = []
    legacy: list[dict] = []
    for hook in hooks:
        if not points_at(hook.get('url'), GITLAB_WEBHOOK_URL):
            continue
        identity = hook_identity(hook['url'])
        if identity is None:
            legacy.append(hook)
        elif identity == webhook.webhook_uuid:
            current.append(hook)
        else:
            stale.append(hook)
    # A second hook with the current identity would deliver every event twice.
    stale.extend(current[1:])

    converted = False
    if legacy and not current:
        converted = bool(
            await install_claimed_webhook(
                gitlab_service=gitlab_service,
                resource_type=resource_type,
                resource_id=resource_id,
                webhook_store=webhook_store,
                webhook=webhook,
                run_id=run_id,
            )
        )
        if not converted:
            raise BreakLoopException()
    if legacy:
        stale.extend(legacy)
    elif not current:
        # Nothing on GitLab carries the row's identity; the installer recreates it.
        await webhook_store.update_claimed(webhook.id, run_id, webhook_exists=False)

    deleted = 0
    for hook in stale:
        if await gitlab_service.delete_hook(resource_type, resource_id, hook['id']):
            break
        deleted += 1

    logger.info(
        'gitlab_webhook.repaired',
        extra={
            'webhook_row_id': webhook.id,
            'resource_id': resource_id,
            'resource_type': resource_type,
            'abandoned_intents': abandoned,
            'stale_hooks': len(stale),
            'deleted_hooks': deleted,
            'converted_legacy': converted,
        },
    )
