"""SQLAlchemy model for team membership.

Membership is the authorization source for the team settings layer. The
composite foreign keys make the identity explicit and tenant-safe at the
database boundary:

* ``(org_id, team_id)`` must name a team in the same organization.
* ``(org_id, user_id)`` must name an existing organization member.

A row therefore cannot describe a member of tenant A in a team of tenant B,
even if a caller supplies a valid team id from another organization.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class TeamMember(Base):
    """Explicit membership of an organization member in one team."""

    __tablename__ = 'team_member'
    __table_args__ = (
        ForeignKeyConstraint(
            ['org_id', 'team_id'],
            ['team.org_id', 'team.id'],
            name='fk_team_member_team',
            ondelete='CASCADE',
        ),
        ForeignKeyConstraint(
            ['org_id', 'user_id'],
            ['org_member.org_id', 'org_member.user_id'],
            name='fk_team_member_org_member',
            ondelete='CASCADE',
        ),
    )

    org_id: Mapped[UUID] = mapped_column(primary_key=True)
    team_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
