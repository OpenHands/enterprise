from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class AzureDevOpsWebhook(Base):
    """The org-wide Azure DevOps resolver Service Hooks for one organization.

    Azure DevOps hides Service Hooks from members who can't view them, so this
    record is how those members learn that, and when, the hooks were set up.
    ``organization`` is the casefolded Azure DevOps organization name.
    ``installed_at`` and ``installed_by_user_id`` are empty when the hooks were
    found already installed rather than installed through OpenHands.
    """

    __tablename__ = 'azure_devops_webhook'
    __table_args__ = (
        UniqueConstraint('organization', name='uq_azure_devops_webhook_organization'),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    organization: Mapped[str] = mapped_column(String, nullable=False)
    installed_by_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    installed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pr_subscription_id: Mapped[str | None] = mapped_column(String, nullable=True)
    work_item_subscription_id: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:
        return (
            f'<AzureDevOpsWebhook(id={self.id}, organization={self.organization}, '
            f'installed_at={self.installed_at})>'
        )
