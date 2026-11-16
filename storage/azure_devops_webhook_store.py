from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select

from storage.azure_devops_webhook import AzureDevOpsWebhook
from storage.database import a_session_maker


def _key(organization: str) -> str:
    # Azure DevOps organization names are case-insensitive.
    return organization.casefold()


@dataclass
class AzureDevOpsWebhookStore:
    """Records the org-wide resolver Service Hooks of Azure DevOps organizations."""

    async def get_webhook(self, organization: str) -> AzureDevOpsWebhook | None:
        async with a_session_maker() as session:
            result = await session.execute(
                select(AzureDevOpsWebhook)
                .where(AzureDevOpsWebhook.organization == _key(organization))
                .limit(1)
            )
            return result.scalars().first()

    async def record_install(
        self,
        organization: str,
        *,
        user_id: str,
        pr_subscription_id: str | None,
        work_item_subscription_id: str | None,
    ) -> AzureDevOpsWebhook:
        """Record hooks just installed through OpenHands by ``user_id``."""
        now = datetime.now(timezone.utc)
        return await self._upsert(
            organization,
            pr_subscription_id=pr_subscription_id,
            work_item_subscription_id=work_item_subscription_id,
            installed_by_user_id=user_id,
            installed_at=now,
            last_verified_at=now,
        )

    async def record_verified(
        self,
        organization: str,
        *,
        pr_subscription_id: str | None,
        work_item_subscription_id: str | None,
    ) -> AzureDevOpsWebhook:
        """Record that a user who can view the hooks saw them installed.

        Keeps the original install details; creates a record without them when
        the hooks were installed some other way.
        """
        return await self._upsert(
            organization,
            pr_subscription_id=pr_subscription_id,
            work_item_subscription_id=work_item_subscription_id,
            last_verified_at=datetime.now(timezone.utc),
        )

    async def delete_webhook(self, organization: str) -> bool:
        async with a_session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    delete(AzureDevOpsWebhook).where(
                        AzureDevOpsWebhook.organization == _key(organization)
                    )
                )
                return result.rowcount > 0

    async def _upsert(self, organization: str, **values) -> AzureDevOpsWebhook:
        async with a_session_maker() as session:
            async with session.begin():
                result = await session.execute(
                    select(AzureDevOpsWebhook)
                    .where(AzureDevOpsWebhook.organization == _key(organization))
                    .limit(1)
                    .with_for_update()
                )
                webhook = result.scalars().first()
                if webhook is None:
                    webhook = AzureDevOpsWebhook(
                        organization=_key(organization), **values
                    )
                    session.add(webhook)
                else:
                    for name, value in values.items():
                        setattr(webhook, name, value)
            await session.refresh(webhook)
            return webhook
