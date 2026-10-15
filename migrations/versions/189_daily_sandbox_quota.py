"""Daily sandbox quota: no schema change (legacy names kept for rolling deploys).

The metered unit for the daily quota changes from conversations to sandbox
creations: a sandbox is what allocates infrastructure. Only the Python-side
names change (``DailySandboxUsage``, ``sandbox_count``, ``daily_sandbox_limit``);
the physical schema keeps its legacy names:

- table ``daily_conversation_usage`` (model ``__tablename__``)
- column ``daily_conversation_usage.conversation_count``
- columns ``user.daily_conversation_limit`` / ``org.daily_conversation_limit``

Renaming the physical table/columns would break every pod still running the
previous image during a rolling deploy: saas-deploy runs ``alembic upgrade
head`` from the new pods' init containers while old pods are still serving,
and those pods map the legacy names, so their ``select(User)`` / ``select(Org)``
queries would fail with undefined-column errors for the whole rollout (and a
rollback would need a manual downgrade before the old image could serve).
Keeping the legacy names means old and new code run against the same schema
in any order, and usage history / limit overrides (including -1 exemptions)
carry over untouched with no data movement.

This migration is therefore an intentional no-op that only advances the
revision. The physical renames can land in a later migration once no
pre-189 pods remain.
"""

from typing import Sequence

revision: str = '189'
down_revision: str | None = '188'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
