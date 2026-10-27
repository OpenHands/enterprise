"""Store agent profiles' empty ``tools`` as unset.

``[]`` selects no tools while ``null`` selects the standard set; existing
``[]`` rows were saved meaning the standard set.

Revision ID: 200
Revises: 199
Create Date: 2026-09-30 00:00:00.000000
"""

import json
import logging
from typing import Any, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '200'
down_revision: str | None = '199'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)


def _schema_version(profile: dict[str, Any]) -> int:
    version = profile.get('schema_version', 1)
    if type(version) is int and version in (1, 2):
        return version
    return 3


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
    scanned = migrated = skipped = 0
    for row in list(rows):
        scanned += 1
        try:
            agent_profiles = json.loads(decrypt_value(row['agent_profiles']))
            if not isinstance(agent_profiles, dict) or not _unset_empty_tools(
                agent_profiles
            ):
                continue
        except Exception:
            skipped += 1
            logger.warning('Skipping org %s: unreadable agent_profiles', row['id'])
            continue
        bind.execute(
            sa.text('UPDATE org SET agent_profiles = :agent_profiles WHERE id = :id'),
            {
                'agent_profiles': encrypt_value(json.dumps(agent_profiles)),
                'id': row['id'],
            },
        )
        migrated += 1
    logger.info(
        'Agent profile tool migration: scanned=%s migrated=%s unreadable=%s',
        scanned,
        migrated,
        skipped,
    )


def downgrade() -> None:
    pass
