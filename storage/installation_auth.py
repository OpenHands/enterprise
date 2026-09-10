"""Durable installation authentication and bootstrap history."""

from uuid import UUID

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class InstallationAuth(Base):
    __tablename__ = 'installation_auth'

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(String(16))
    # Historical identity, deliberately no FK: deleting the original administrator
    # must never erase bootstrap history or authorize another bootstrap.
    bootstrap_admin_id: Mapped[UUID | None] = mapped_column()
    bootstrap_complete: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (
        CheckConstraint('id = 1', name='ck_installation_auth_singleton'),
        CheckConstraint(
            "mode IN ('local', 'keycloak')", name='ck_installation_auth_mode'
        ),
        CheckConstraint(
            'NOT bootstrap_complete OR bootstrap_admin_id IS NOT NULL',
            name='ck_installation_auth_bootstrap',
        ),
    )
