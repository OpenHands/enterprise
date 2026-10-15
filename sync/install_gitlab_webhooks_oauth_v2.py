"""GitLab webhook installer for the procrastinate worker, without Keycloak.

The worker's ``install_gitlab_webhooks`` scheduled job runs this module; the
Kubernetes CronJob keeps running ``sync.install_gitlab_webhooks``, which reads
Keycloak tokens. Here every token comes from ``oauth_tokens``, and Keycloak is
never called. A user with no such token is skipped: failing their checks
would delete their webhook rows.
"""

from __future__ import annotations

import asyncio

from integrations.gitlab.gitlab_service import OAuthV2GitLabService
from openhands.app_server.utils.logger import openhands_logger as logger
from sync.install_gitlab_webhooks import VerifyWebhookStatus


class OAuthV2VerifyWebhookStatus(VerifyWebhookStatus):
    async def gitlab_service_for(self, user_id: str) -> OAuthV2GitLabService | None:
        service = await OAuthV2GitLabService.for_user(user_id)
        if service is None:
            logger.info(
                'gitlab_webhook_skipped_without_oauth_v2_token',
                extra={'user_id': user_id},
            )
        return service


if __name__ == '__main__':
    asyncio.run(OAuthV2VerifyWebhookStatus().install_webhooks())
