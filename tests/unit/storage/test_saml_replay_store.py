"""PostgreSQL atomicity, contention, and bounded cleanup for SAML replay IDs."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text

from tests.unit.server.routes.saml_signed_helpers import bounded, make_provider


@pytest.fixture
async def replay_store(async_session_maker):
    from storage.saml_replay_store import SamlReplayStore

    provider = await make_provider(async_session_maker)
    with patch('storage.saml_replay_store.a_session_maker', async_session_maker):
        yield SamlReplayStore, provider.id, async_session_maker


async def rows(maker):
    async with maker() as session:
        return (
            (
                await bounded(
                    session.execute(text('SELECT * FROM saml_consumed_message'))
                )
            )
            .mappings()
            .all()
        )


@pytest.mark.parametrize('conflict', ['both', 'request', 'assertion'])
async def test_consume_conflicts_roll_back_every_insert(replay_store, conflict):
    store, provider, maker = replay_store
    expiry = datetime.utcnow() + timedelta(hours=1)
    assert await bounded(store.consume(provider, 'request', 'assertion', expiry))
    request = 'new-request' if conflict == 'assertion' else 'request'
    assertion = 'new-assertion' if conflict == 'request' else 'assertion'
    assert not await bounded(store.consume(provider, request, assertion, expiry))
    assert len(await rows(maker)) == 2


async def test_ten_competitors_have_one_winner(replay_store):
    store, provider, maker = replay_store
    results = await bounded(
        asyncio.gather(
            *(store.consume(provider, 'r', 'a', datetime.utcnow()) for _ in range(10))
        )
    )
    assert results.count(True) == 1
    assert len(await rows(maker)) == 2


async def wait_for_blocker(maker, pid):
    async def poll():
        while True:
            async with maker() as observer:
                count = (
                    await bounded(
                        observer.execute(
                            text(
                                'SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND :pid = ANY(pg_blocking_pids(pid))'
                            ),
                            {'pid': pid},
                        )
                    )
                ).scalar_one()
            if count == 1:
                return
            await asyncio.sleep(0.05)

    await asyncio.wait_for(poll(), 5)


@pytest.mark.parametrize('commit', [True, False])
async def test_consume_waits_for_conflicting_transaction(replay_store, commit):
    store, provider, maker = replay_store
    session = maker()
    task = None
    try:
        pid = (
            await bounded(session.execute(text('SELECT pg_backend_pid()')))
        ).scalar_one()
        await bounded(
            session.execute(
                text(
                    "INSERT INTO saml_consumed_message (provider_id, message_kind, message_id, expires_at) VALUES (:provider, 'request', 'r', :expiry)"
                ),
                {'provider': provider, 'expiry': datetime.utcnow()},
            )
        )
        task = asyncio.create_task(store.consume(provider, 'r', 'a', datetime.utcnow()))
        await wait_for_blocker(maker, pid)
        await bounded(session.commit() if commit else session.rollback())
        assert await bounded(task) is (not commit)
    finally:
        await bounded(session.rollback())
        await bounded(session.close())
        if task and not task.done():
            task.cancel()
            await bounded(asyncio.gather(task, return_exceptions=True))


async def test_cleanup_is_bounded_and_skips_locked_rows(replay_store):
    store, provider, maker = replay_store
    now = datetime.utcnow()
    for i, expiry in enumerate(
        [now - timedelta(minutes=1), now, now + timedelta(minutes=1)]
    ):
        assert await bounded(store.consume(provider, f'r{i}', f'a{i}', expiry))
    session = maker()
    try:
        pid = (
            await bounded(session.execute(text('SELECT pg_backend_pid()')))
        ).scalar_one()
        locked = (
            await bounded(
                session.execute(
                    text(
                        'SELECT id FROM saml_consumed_message ORDER BY expires_at, id LIMIT 1 FOR UPDATE'
                    )
                )
            )
        ).scalar_one()
        async with maker() as observer:
            assert (
                await bounded(
                    observer.execute(
                        text(
                            'SELECT count(*) FROM pg_stat_activity WHERE pid = :pid AND datname = current_database() AND xact_start IS NOT NULL'
                        ),
                        {'pid': pid},
                    )
                )
            ).scalar_one() == 1
        assert await asyncio.wait_for(store.delete_expired(now, batch_size=1), 2) == 1
        remaining = await rows(maker)
        assert len(remaining) == 5
        assert any(row['id'] == locked for row in remaining)
        assert await asyncio.wait_for(store.delete_expired(now), 2) == 0
    finally:
        await bounded(session.rollback())
        await bounded(session.close())
    assert await bounded(store.delete_expired(now)) == 1
    assert len(await rows(maker)) == 4


async def test_expired_conflicts_fail_closed_and_providers_are_independent(
    replay_store,
):
    store, provider, maker = replay_store
    expired = datetime.utcnow() - timedelta(days=1)
    assert await bounded(store.consume(provider, 'r', 'a', expired))
    assert not await bounded(store.consume(provider, 'r', 'a', expired))
    other = await make_provider(maker)
    assert await bounded(store.consume(other.id, 'r', 'a', expired))
    assert len(await rows(maker)) == 4
