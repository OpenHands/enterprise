"""Store agent profiles' empty ``tools`` as unset.

``[]`` selects no tools while ``null`` selects the standard set; existing
``[]`` rows were saved meaning the standard set.

Revision ID: 175
Revises: 174
Create Date: 2026-09-30 00:00:00.000000
"""

import json
import logging
from typing import Any, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '175'
down_revision: str | None = '174'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)


def _schema_version(profile: dict[str, Any]) -> int:
    version = profile.get('schema_version')
    if isinstance(version, int) and not isinstance(version, bool):
        return version
    return 1


def _unset_empty_tools(agent_profiles: dict[str, Any]) -> bool:
    changed = False
    for profile in (agent_profiles.get('profiles') or {}).values():
        if (
            isinstance(profile, dict)
            and profile.get('agent_kind', 'openhands') == 'openhands'
            and profile.get('tools') == []
            and _schema_version(profile) < 3
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
        try:
            agent_profiles = json.loads(decrypt_value(row['agent_profiles']))
            if not isinstance(agent_profiles, dict) or not _unset_empty_tools(
                agent_profiles
            ):
                continue
        except Exception:
            logger.warning(
                'Skipping org %s: unreadable agent_profiles', row['id'], exc_info=True
            )
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
