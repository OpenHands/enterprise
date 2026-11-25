"""Consolidate org and org_member settings blocks into one JSON document.

Revision ID: 223
Revises: 222
Create Date: 2026-11-22 00:00:00.000000

Each org and each org_member row now owns a single ``settings`` document::

    {"schema_version": 1, "agent_settings": {...}, "conversation_settings": {...}}

The legacy columns were projections of those two blocks, so the backfill is a
pure copy.

Credential safety
-----------------
The legacy plaintext columns could still hold an inline ``mcp_config`` or
``llm.api_key`` from before those moved to encrypted columns. The new document
must never receive plaintext credentials, and a populated encrypted column does
not prove that its value matches the inline copy (that would require decrypting
in a migration). So before any DDL runs this migration counts *meaningful*
inline credentials:

* ``llm.api_key`` that is not JSON null, blank/whitespace, or a ``***`` mask
* ``mcp_config`` that is not JSON null and not an empty object

Placeholders such as ``null``, ``''``, ``**********`` and ``{}`` are ordinary
row data, not secrets, and are copied through unchanged. If any real inline
credential exists, the migration aborts the whole transaction with per-table
counts and remediation instructions. It never silently discards a credential,
whether or not an encrypted owner column happens to be populated.

The downgrade restores both legacy columns from the document unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '223'
down_revision: str | None = '222'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EMPTY_JSON = sa.text("'{}'::json")
_SCHEMA_VERSION = 1


def _agent_block(column: str) -> str:
    """Copy a legacy agent block verbatim."""
    return f"COALESCE({column}::jsonb, '{{}}'::jsonb)"


def _block_expression(document_column: str, key: str) -> str:
    """Read an object block from the document, defaulting to an empty object."""
    document = f"COALESCE({document_column}::jsonb, '{{}}'::jsonb)"
    return (
        f"CASE WHEN jsonb_typeof({document} -> '{key}') = 'object' "
        f"THEN {document} -> '{key}' ELSE '{{}}'::jsonb END"
    )


def _real_llm_key_predicate(column: str) -> str:
    """Rows whose inline ``llm.api_key`` is a real credential.

    JSON null, blank/whitespace strings and ``***`` masks are placeholders that
    ordinary rows carry; non-string values are treated as suspicious.
    """
    value = f"({column}::jsonb #> '{{llm,api_key}}')"
    text_value = f"({value} #>> '{{}}')"
    return (
        f"jsonb_typeof({column}::jsonb -> 'llm') = 'object' "
        f"AND ({column}::jsonb -> 'llm') ? 'api_key' "
        f"AND NOT ({value} = 'null'::jsonb) "
        f"AND NOT (jsonb_typeof({value}) = 'string' "
        f"AND (btrim({text_value}) = '' OR btrim({text_value}) ~ '^[*]+$'))"
    )


def _real_mcp_config_predicate(column: str) -> str:
    """Rows whose inline ``mcp_config`` is a real configuration."""
    value = f"({column}::jsonb -> 'mcp_config')"
    return (
        f"({column}::jsonb ? 'mcp_config' "
        f"AND NOT ({value} = 'null'::jsonb OR {value} = '{{}}'::jsonb))"
    )


def _secret_counts(bind: sa.Connection, *, table: str, column: str) -> dict[str, int]:
    """Count meaningful inline credentials in one legacy block."""
    statement = sa.text(
        f"""
        SELECT
            count(*) FILTER (
                WHERE {_real_llm_key_predicate(column)}
            )::int AS real_llm_keys,
            count(*) FILTER (
                WHERE {_real_mcp_config_predicate(column)}
            )::int AS real_mcp_configs
        FROM {table}
        """
    )
    return dict(bind.execute(statement).mappings().one())


def _assert_no_real_secrets(bind: sa.Connection) -> None:
    """Abort before touching the schema when a real credential is inline."""
    counts: dict[str, dict[str, int]] = {
        'org': _secret_counts(bind, table='org', column='agent_settings'),
        'org_member': _secret_counts(
            bind, table='org_member', column='agent_settings_diff'
        ),
    }

    problems: list[str] = []
    for table, table_counts in counts.items():
        if table_counts['real_llm_keys']:
            problems.append(
                f'{table}: {table_counts["real_llm_keys"]} row(s) carry a '
                f'real inline llm.api_key'
            )
        if table_counts['real_mcp_configs']:
            problems.append(
                f'{table}: {table_counts["real_mcp_configs"]} row(s) carry a '
                f'real inline mcp_config'
            )

    if problems:
        remediation = (
            'Remediate before upgrading: move each inline credential into its '
            "encrypted owner column (the row's llm_api_key or mcp_config), or "
            'remove it if it is no longer needed, then rerun the migration. '
            'Placeholders (null, empty string, *** mask, empty mcp_config) do '
            'not need remediation.'
        )
        raise RuntimeError(
            'Migration 223 aborted: legacy plaintext settings contain real '
            'credentials. ' + '; '.join(problems) + '. ' + remediation
        )


def upgrade() -> None:
    bind = op.get_bind()
    _assert_no_real_secrets(bind)

    op.add_column(
        'org',
        sa.Column('settings', sa.JSON(), nullable=False, server_default=_EMPTY_JSON),
    )
    op.add_column(
        'org_member',
        sa.Column('settings', sa.JSON(), nullable=False, server_default=_EMPTY_JSON),
    )

    op.execute(
        sa.text(
            f"""
            UPDATE org
            SET settings = jsonb_build_object(
                'schema_version', {_SCHEMA_VERSION},
                'agent_settings', {_agent_block('agent_settings')},
                'conversation_settings',
                    COALESCE(conversation_settings::jsonb, '{{}}'::jsonb)
            )::json
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE org_member
            SET settings = jsonb_build_object(
                'schema_version', {_SCHEMA_VERSION},
                'agent_settings', {_agent_block('agent_settings_diff')},
                'conversation_settings',
                    COALESCE(conversation_settings_diff::jsonb, '{{}}'::jsonb)
            )::json
            """
        )
    )

    op.alter_column('org', 'settings', server_default=None)
    op.alter_column('org_member', 'settings', server_default=None)

    op.drop_column('org', 'conversation_settings')
    op.drop_column('org', 'agent_settings')
    op.drop_column('org_member', 'conversation_settings_diff')
    op.drop_column('org_member', 'agent_settings_diff')


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
    """Use the migration predicate without applying DDL or leaking row contents."""
    with engine.begin() as bind:
        bind.execute(sa.text('SET TRANSACTION READ ONLY'))
        _assert_no_real_secrets(bind)


if __name__ == '__main__':
    from storage.database import get_engine

    preflight(get_engine())
    print('Settings preflight passed: no blocking inline credentials.')
