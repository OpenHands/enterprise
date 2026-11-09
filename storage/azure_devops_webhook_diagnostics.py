from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base
from storage.database import a_session_maker


class AzureDevOpsWebhookDiagnostics(Base):
    """Latest install attempt and readable Azure observation, retained after uninstall."""

    __tablename__ = 'azure_devops_webhook_diagnostics'

    organization: Mapped[str] = mapped_column(String, primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_install_attempt: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_observation: Mapped[dict[str, Any] | None] = mapped_column(JSON)


async def save_webhook_diagnostics(
    organization: str,
    *,
    last_install_attempt: dict[str, Any] | None = None,
    last_observation: dict[str, Any] | None = None,
) -> None:
    values: dict[str, Any] = {'updated_at': datetime.now(timezone.utc)}
    if last_install_attempt is not None:
        values['last_install_attempt'] = last_install_attempt
    if last_observation is not None:
        values['last_observation'] = last_observation
    statement = insert(AzureDevOpsWebhookDiagnostics).values(
        organization=organization.casefold(), **values
    )
    async with a_session_maker() as session, session.begin():
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=['organization'], set_=values
            )
        )
