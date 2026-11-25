"""Which organizations may use which private sandbox specs.

Sandbox specs are warm runtime configs owned by runtime-api, so these tables
reference them by name without a foreign key. A spec with no row is public.
"""

from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class StoredSandboxSpecAccess(Base):
    __tablename__ = 'sandbox_spec_access'

    sandbox_name: Mapped[str] = mapped_column(String, primary_key=True)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False)


class StoredSandboxSpecOrgGrant(Base):
    __tablename__ = 'sandbox_spec_org_grant'

    sandbox_name: Mapped[str] = mapped_column(
        ForeignKey('sandbox_spec_access.sandbox_name', ondelete='CASCADE'),
        primary_key=True,
    )
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey('org.id', ondelete='CASCADE'), primary_key=True, index=True
    )
