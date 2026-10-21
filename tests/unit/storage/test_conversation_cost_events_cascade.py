"""Tests for conversation soft deletion and cost-event retention.

The foreign key retains ``ON DELETE CASCADE`` for explicit hard-delete paths,
while user-initiated conversation deletion marks the metadata row deleted and
preserves its cost events for reconciliation and auditing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    SQLAppConversationInfoService,
    StoredConversationCostEvent,
    StoredConversationMetadata,
)
from openhands.app_server.user.specifiy_user_context import SpecifyUserContext


@pytest.fixture
async def session(async_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    session_maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with session_maker() as db_session:
        yield db_session


def test_cost_event_fk_declares_cascade():
    """The model FK must declare ``ondelete='CASCADE'``.

    Without this, any fresh database (tests, dev, on-prem deploys that
    re-create the schema) ends up with the same broken constraint the
    production migration is fixing.
    """
    conversation_id_column = StoredConversationCostEvent.__table__.c.conversation_id
    foreign_keys = list(conversation_id_column.foreign_keys)
    assert len(foreign_keys) == 1
    fk = foreign_keys[0]
    assert fk.column.table.name == 'conversation_metadata'
    assert fk.ondelete == 'CASCADE'


@pytest.mark.asyncio
async def test_soft_delete_conversation_retains_row_and_cost_events(
    async_engine: AsyncEngine, session: AsyncSession
):
    """Soft-deleting a conversation retains its row and cost-event rows.

    Production no longer hard-deletes ``conversation_metadata``; it marks
    ``deleted_at``. The row and its ``conversation_cost_events`` rows are kept
    for reconciliation/audit, and the conversation is hidden from reads.
    """
    conversation_id = str(uuid4())
    other_conversation_id = str(uuid4())

    session.add(
        StoredConversationMetadata(
            conversation_id=conversation_id,
            created_at=datetime.now(timezone.utc),
            last_updated_at=datetime.now(timezone.utc),
        )
    )
    session.add(
        StoredConversationMetadata(
            conversation_id=other_conversation_id,
            created_at=datetime.now(timezone.utc),
            last_updated_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()

    now = datetime.now(timezone.utc)
    session.add_all(
        [
            StoredConversationCostEvent(
                conversation_id=conversation_id, cost_delta=0.10, occurred_at=now
            ),
            StoredConversationCostEvent(
                conversation_id=conversation_id, cost_delta=0.25, occurred_at=now
            ),
            StoredConversationCostEvent(
                conversation_id=other_conversation_id,
                cost_delta=0.99,
                occurred_at=now,
            ),
        ]
    )
    await session.commit()

    service = SQLAppConversationInfoService(
        db_session=session, user_context=SpecifyUserContext(user_id=None)
    )
    deleted = await service.delete_app_conversation_info(UUID(conversation_id))
    assert deleted is True

    # The conversation row is retained (soft-deleted) with deleted_at set.
    metadata = (
        await session.execute(
            select(
                StoredConversationMetadata.conversation_id,
                StoredConversationMetadata.deleted_at,
            )
        )
    ).all()
    metadata_by_id = {row[0]: row[1] for row in metadata}
    assert metadata_by_id[conversation_id] is not None  # soft-deleted
    assert metadata_by_id[other_conversation_id] is None  # untouched

    # Cost-event rows are retained (no cascade removal on soft-delete).
    remaining_cost_events = (
        (await session.execute(select(StoredConversationCostEvent.conversation_id)))
        .scalars()
        .all()
    )
    assert set(remaining_cost_events) == {conversation_id, other_conversation_id}

    # The soft-deleted conversation is hidden from reads.
    assert await service.get_app_conversation_info(UUID(conversation_id)) is None
