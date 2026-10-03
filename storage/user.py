"""
SQLAlchemy model for User.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from storage.base import Base
from storage.encrypt_utils import EncryptedJSON

if TYPE_CHECKING:
    from storage.org import Org
    from storage.org_member import OrgMember
    from storage.role import Role
    from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas


class User(Base):
    """User model with organizational relationships.

    This model satisfies the UserBase protocol via structural typing.
    """

    __tablename__ = 'user'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    current_org_id: Mapped[UUID] = mapped_column(ForeignKey('org.id'), nullable=False)
    # Instance-level super role; org membership roles live on OrgMember.role_id.
    # Effective permissions are defined by SUPER_ROLE_PERMISSIONS.
    role_id: Mapped[int | None] = mapped_column(ForeignKey('role.id'), nullable=True)
    accepted_tos: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enable_sound_notifications: Mapped[bool | None] = mapped_column(nullable=True)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    user_consents_to_analytics: Mapped[bool | None] = mapped_column(nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    email_verified: Mapped[bool | None] = mapped_column(nullable=True)
    git_user_name: Mapped[str | None] = mapped_column(String, nullable=True)
    git_user_email: Mapped[str | None] = mapped_column(String, nullable=True)
    git_full_clone: Mapped[bool | None] = mapped_column(nullable=True, default=False)
    sandbox_grouping_strategy: Mapped[str | None] = mapped_column(String, nullable=True)
    default_sandbox_spec_id: Mapped[str | None] = mapped_column(String, nullable=True)
    disabled_skills: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    llm_profiles: Mapped[dict[str, Any] | None] = mapped_column(
        EncryptedJSON, nullable=True
    )
    onboarding_completed: Mapped[bool | None] = mapped_column(
        nullable=True, default=False
    )
    # NULL inherits the deployment-wide daily conversation limit.
    daily_conversation_limit: Mapped[int | None] = mapped_column(nullable=True)
    # Work email submitted for quota increase requests; verified via
    # signed email link before the requested limit is applied.
    work_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    work_email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Enterprise persistent-memory: when True, the MemoryChangeCallbackProcessor
    # is registered for the user's conversations and the stored ``memory_context``
    # is injected into each sandbox at conversation start.
    enable_memory_context: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default='false'
    )
    # The latest MEMORY.md content captured by the callback. Written by the
    # callback on every successful file_editor edit; read at conversation start
    # to seed the sandbox's memory file before the agent runs.
    memory_context: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # One-time identity-seeding flag for IDP swap-over (OHE-3293 / ALL-5978).
    # When True, the v2 login resolver may use ``email`` as a one-time hint to
    # bind a new IDP's ``sub`` to this existing User via ``oauth_provider_users``.
    # The flag is self-cleared on a successful link, so the email-match window
    # is exactly one login wide per user. Off by default; bulk-set by an
    # operator at the swap-over moment.
    allow_match_by_email: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default='false'
    )

    # Relationships
    # Instance-level super-role relationship, not an org-scoped membership role.
    role: Mapped['Role | None'] = relationship('Role', back_populates='users')
    org_members: Mapped[list['OrgMember']] = relationship(
        'OrgMember', back_populates='user'
    )
    current_org: Mapped['Org'] = relationship('Org', back_populates='current_users')
    stored_conversation_metadata_saas: Mapped[
        list['StoredConversationMetadataSaas']
    ] = relationship('StoredConversationMetadataSaas', back_populates='user')

    def sync_analytics_consent_with_tos(self) -> None:
        self.user_consents_to_analytics = self.accepted_tos is not None
