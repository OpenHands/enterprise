"""Periodic cleanup of stale app_conversation_start_task rows.

The start-task table records the (potentially slow) process of starting an app
conversation. Once a conversation is started or the start fails, the row is no
longer needed, but it is only deleted when the associated conversation is
explicitly deleted. Rows for conversations that were never created or never
deleted therefore accumulate forever.

This script deletes start-task rows older than ``OLDER_THAN_DAYS`` days. It is
intended to run as a Kubernetes CronJob (see the OpenHands-Cloud chart) once a
day, mirroring the ``clean_proactive_convo_table`` pattern.

Deletes are performed in batches (``BATCH_SIZE`` rows per transaction) to bound
the work done per transaction, limit lock-table entry consumption, and allow
autovacuum to reclaim dead tuples incrementally. This matters on the first run
against a large backlog (the table has grown to ~1 GB in production). After the
first run, a manual ``VACUUM (ANALYZE) app_conversation_start_task`` is
recommended to physically reclaim the reclaimed space.
"""

import asyncio  # noqa: I001

# This must be before the import of storage to set up logging and prevent
# alembic from running its mouth.
from openhands.app_server.utils.logger import openhands_logger

from storage.database import a_session_maker

OLDER_THAN_DAYS = 1
BATCH_SIZE = 1000


async def main() -> None:
    from datetime import UTC, datetime, timedelta

    from openhands.app_server.app_conversation.sql_app_conversation_start_task_service import (
        SQLAppConversationStartTaskService,
    )

    cutoff = datetime.now(UTC) - timedelta(days=OLDER_THAN_DAYS)
    # created_at is stored as naive UTC; strip tzinfo before comparing.
    cutoff_naive = cutoff.replace(tzinfo=None)

    openhands_logger.info(
        'clean_app_conversation_start_tasks',
        extra={
            'older_than_days': OLDER_THAN_DAYS,
            'cutoff': cutoff_naive.isoformat(),
            'batch_size': BATCH_SIZE,
        },
    )

    async with a_session_maker() as session:
        service = SQLAppConversationStartTaskService(session=session)
        deleted = await service.delete_start_tasks_older_than(
            cutoff_naive, batch_size=BATCH_SIZE
        )

    openhands_logger.info(
        f'Deleted {deleted} app_conversation_start_task rows older than '
        f'{OLDER_THAN_DAYS} day(s)'
    )


if __name__ == '__main__':
    asyncio.run(main())
