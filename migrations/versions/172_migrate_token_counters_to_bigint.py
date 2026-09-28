"""Migrate token counter columns from INTEGER to BIGINT.

The conversation webhook callback writes accumulated token counts to the
``conversation_metadata`` and ``conversation_cost_events`` tables. These
columns were originally created as ``INTEGER`` (PostgreSQL 32-bit, max
~2.1 billion). Long-running conversations can accumulate token counts that
exceed INT32, causing the DB write to fail and the webhook to return a 500
(OHE-3391).

This migration alters every token counter column to ``BIGINT`` (64-bit,
max ~9.2 quintillion). In PostgreSQL this is a safe in-place widening cast
with no data loss, so no table rewrite or data backfill is needed.

Revision ID: 172
Revises: 171
Create Date: 2026-09-28 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '172'
down_revision: str | None = '171'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Token counter columns on conversation_metadata that accumulate and can
# exceed INT32 across long conversations.
_CONVERSATION_METADATA_TOKEN_COLUMNS = [
    'prompt_tokens',
    'completion_tokens',
    'total_tokens',
    'cache_read_tokens',
    'cache_write_tokens',
    'reasoning_tokens',
    'context_window',
    'per_turn_token',
]

# Per-event token deltas on conversation_cost_events.
_COST_EVENT_TOKEN_COLUMNS = [
    'prompt_tokens',
    'completion_tokens',
]


def upgrade() -> None:
    for column_name in _CONVERSATION_METADATA_TOKEN_COLUMNS:
        op.alter_column(
            'conversation_metadata',
            column_name,
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
            existing_server_default=sa.text('0'),
        )

    for column_name in _COST_EVENT_TOKEN_COLUMNS:
        op.alter_column(
            'conversation_cost_events',
            column_name,
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Narrowing BIGINT -> INTEGER is only safe when no stored value exceeds
    # INT32. We restore the original schema shape for completeness; if any
    # value exceeds the 32-bit range the cast will fail loudly.
    for column_name in _COST_EVENT_TOKEN_COLUMNS:
        op.alter_column(
            'conversation_cost_events',
            column_name,
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )

    for column_name in _CONVERSATION_METADATA_TOKEN_COLUMNS:
        op.alter_column(
            'conversation_metadata',
            column_name,
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
            existing_server_default=sa.text('0'),
        )
