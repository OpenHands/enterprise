"""Consolidate org and org_member settings blocks into one JSON document.

Revision ID: 223
Revises: 222
Create Date: 2026-11-22 00:00:00.000000

Convert legacy org/member JSON into sparse owner contributions. Preflight
uses the same conversion as the upgrade. Existing credential columns are not
read or rewritten. Restore the pre-window backup for an exact rollback;
downgrade only restores the legacy column layout with converted values.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '223'
down_revision: str | None = '222'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EMPTY_JSON = sa.text("'{}'::json")


def _block_expression(document_column: str, key: str) -> str:
    return f"COALESCE({document_column}::jsonb -> '{key}', '{{}}'::jsonb)"


_ORG = sa.table(
    'org',
    sa.column('id', sa.Uuid()),
    sa.column('agent_settings', sa.JSON()),
    sa.column('conversation_settings', sa.JSON()),
    sa.column('settings', sa.JSON()),
)
_MEMBER = sa.table(
    'org_member',
    sa.column('org_id', sa.Uuid()),
    sa.column('user_id', sa.Uuid()),
    sa.column('agent_settings_diff', sa.JSON()),
    sa.column('conversation_settings_diff', sa.JSON()),
    sa.column('settings', sa.JSON()),
)


def _convert_rows(bind: sa.Connection, *, write: bool) -> None:
    from collections import Counter

    from migrations.settings_223 import ConversionError, convert

    issues: Counter[tuple[str, str]] = Counter()

    def checked(table, agent, conversation, parents=None):
        try:
            return convert(agent, conversation, parents)
        except ConversionError as exc:
            issues[table, str(exc)] += 1
        except (ValueError, TypeError, AttributeError, KeyError):
            # Pydantic errors and the underlying values are not safe to log.
            issues[table, 'settings validation failed'] += 1
        return None

    # Keep only one organization's resolved settings in memory. Keyset paging
    # keeps result sets bounded even when the driver buffers every returned row.
    org_cursor = None
    while True:
        orgs = sa.select(_ORG.c.id, _ORG.c.agent_settings, _ORG.c.conversation_settings)
        if org_cursor is not None:
            orgs = orgs.where(_ORG.c.id > org_cursor)
        org_rows = bind.execute(orgs.order_by(_ORG.c.id).limit(500)).all()
        if not org_rows:
            break
        for org_id, agent, conversation in org_rows:
            converted = checked('org', agent, conversation)
            if converted is None:
                continue
            document, parents = converted
            if write:
                bind.execute(
                    _ORG.update().where(_ORG.c.id == org_id).values(settings=document)
                )
            member_cursor = None
            while True:
                members = sa.select(
                    _MEMBER.c.user_id,
                    _MEMBER.c.agent_settings_diff,
                    _MEMBER.c.conversation_settings_diff,
                ).where(_MEMBER.c.org_id == org_id)
                if member_cursor is not None:
                    members = members.where(_MEMBER.c.user_id > member_cursor)
                member_rows = bind.execute(
                    members.order_by(_MEMBER.c.user_id).limit(500)
                ).all()
                if not member_rows:
                    break
                for user_id, agent, conversation in member_rows:
                    converted = checked('org_member', agent, conversation, parents)
                    if write and converted is not None:
                        bind.execute(
                            _MEMBER.update()
                            .where(
                                _MEMBER.c.org_id == org_id,
                                _MEMBER.c.user_id == user_id,
                            )
                            .values(settings=converted[0])
                        )
                member_cursor = member_rows[-1][0]
        org_cursor = org_rows[-1][0]
    if issues:
        counts = '; '.join(
            f'{table}: {count} row(s): {reason}'
            for (table, reason), count in sorted(issues.items())
        )
        raise RuntimeError(
            'Migration 223 stopped: legacy settings require remediation. '
            + counts
            + '. Dependent members are not checked for invalid orgs.'
        )


def upgrade() -> None:
    bind = op.get_bind()
    for table in ('org', 'org_member'):
        op.add_column(
            table, sa.Column('settings', sa.JSON(), server_default=_EMPTY_JSON)
        )
    _convert_rows(bind, write=True)
    for table, suffix in (('org', ''), ('org_member', '_diff')):
        op.alter_column(table, 'settings', nullable=False, server_default=None)
        op.drop_column(table, 'conversation_settings' + suffix)
        op.drop_column(table, 'agent_settings' + suffix)


def downgrade() -> None:
    op.add_column(
        'org',
        sa.Column(
            'agent_settings', sa.JSON(), nullable=False, server_default=_EMPTY_JSON
        ),
    )
    op.add_column(
        'org',
        sa.Column(
            'conversation_settings',
            sa.JSON(),
            nullable=False,
            server_default=_EMPTY_JSON,
        ),
    )
    op.add_column(
        'org_member',
        sa.Column(
            'agent_settings_diff',
            sa.JSON(),
            nullable=False,
            server_default=_EMPTY_JSON,
        ),
    )
    op.add_column(
        'org_member',
        sa.Column(
            'conversation_settings_diff',
            sa.JSON(),
            nullable=False,
            server_default=_EMPTY_JSON,
        ),
    )

    op.execute(
        sa.text(
            f"""
            UPDATE org
            SET agent_settings = {_block_expression('settings', 'agent_settings')},
                conversation_settings =
                    {_block_expression('settings', 'conversation_settings')}
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE org_member
            SET agent_settings_diff =
                    {_block_expression('settings', 'agent_settings')},
                conversation_settings_diff =
                    {_block_expression('settings', 'conversation_settings')}
            """
        )
    )

    op.alter_column('org', 'agent_settings', server_default=None)
    op.alter_column('org', 'conversation_settings', server_default=None)
    op.alter_column('org_member', 'agent_settings_diff', server_default=None)
    op.alter_column('org_member', 'conversation_settings_diff', server_default=None)

    op.drop_column('org', 'settings')
    op.drop_column('org_member', 'settings')


def preflight(engine: sa.Engine) -> None:
    """Rehearse the conversion read-only, reporting counts but no row contents."""
    with engine.begin() as bind:
        bind.execute(sa.text('SET TRANSACTION READ ONLY'))
        _convert_rows(bind, write=False)


if __name__ == '__main__':
    from storage.database import get_engine

    preflight(get_engine())
    print('Settings preflight passed: legacy contributions can be converted.')
