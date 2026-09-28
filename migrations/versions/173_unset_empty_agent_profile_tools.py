"""Store agent profiles' empty ``tools`` as unset.

``[]`` selects no tools while ``null`` selects the standard set; existing
``[]`` rows were saved meaning the standard set.

Revision ID: 173
Revises: 172
Create Date: 2026-09-28 00:00:00.000000
"""

import json
from typing import Any, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '173'
down_revision: str | None = '172'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _unset_empty_tools(agent_profiles: dict[str, Any]) -> bool:
    changed = False
    for profile in (agent_profiles.get('profiles') or {}).values():
        if (
            isinstance(profile, dict)
            and profile.get('agent_kind', 'openhands') == 'openhands'
            and profile.get('tools') == []
        ):
            profile['tools'] = None
            changed = True
    return changed


def upgrade() -> None:
    from storage.encrypt_utils import decrypt_value, encrypt_value

    bind = op.get_bind()
    rows = bind.execute(
        sa.text('SELECT id, agent_profiles FROM org WHERE agent_profiles IS NOT NULL')
    ).mappings()
    for row in list(rows):
        agent_profiles = json.loads(decrypt_value(row['agent_profiles']))
        if not isinstance(agent_profiles, dict) or not _unset_empty_tools(
            agent_profiles
        ):
            continue
        bind.execute(
            sa.text('UPDATE org SET agent_profiles = :agent_profiles WHERE id = :id'),
            {
                'agent_profiles': encrypt_value(json.dumps(agent_profiles)),
                'id': row['id'],
            },
        )


def downgrade() -> None:
    pass
