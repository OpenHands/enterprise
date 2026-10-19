from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from storage.database import a_session_maker
from storage.gitlab_delivery import GitlabDelivery

# Longer than GitLab keeps retrying or queueing a hook's deliveries.
RETENTION = timedelta(days=7)
PRUNE_BATCH = 100


@dataclass
class GitlabDeliveryStore:
    async def record(self, body: bytes) -> bool:
        """Record a delivery by its raw body; False if that body was seen before.

        First deletes a batch of entries older than RETENTION, so the table
        stays bounded without a scheduled job.
        """
        digest = hashlib.sha256(body).hexdigest()
        expired = (
            select(GitlabDelivery.body_sha256)
            .where(GitlabDelivery.received_at < func.now() - RETENTION)
            .limit(PRUNE_BATCH)
            .with_for_update(skip_locked=True)
        )
        async with a_session_maker() as session, session.begin():
            await session.execute(
                delete(GitlabDelivery).where(GitlabDelivery.body_sha256.in_(expired))
            )
            inserted = await session.execute(
                insert(GitlabDelivery)
                .values(body_sha256=digest)
                .on_conflict_do_nothing(index_elements=['body_sha256'])
                .returning(GitlabDelivery.body_sha256)
            )
            is_new = inserted.scalar_one_or_none() is not None
        return is_new
