"""Atomicity, retry, isolation and concurrent-writer contracts on PostgreSQL."""

import asyncio
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select, text

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from storage.org_secrets_store import (
    OrgSecretConflictError,
    OrgSecretNotFoundError,
    OrgSecretsStore,
)
from storage.stored_custom_secrets import StoredCustomSecrets


@pytest.fixture
async def value_store(monkeypatch, async_session_maker, create_org):
    monkeypatch.setattr(
        'storage.org_secrets_store.a_session_maker', async_session_maker
    )
    jwt = JwtService(
        keys=[EncryptionKey(kid='test', key=SecretStr('test-key'), active=True)]
    )
    store = OrgSecretsStore(create_org().id, jwt)
    await store.create_shared('TOKEN', 'fake-old', 'metadata', 'owner')
    return store


async def value(store):
    return (await store.load_shared_values())['TOKEN'].secret.get_secret_value()


@pytest.mark.asyncio
async def test_two_stale_writers_cannot_both_win(value_store):
    store = value_store
    expected = await store.get_value_version('TOKEN')
    results = await asyncio.gather(
        *[
            store.replace_shared_value('TOKEN', candidate, expected, uuid4())
            for candidate in ['fake-writer-a', 'fake-writer-b']
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(r, OrgSecretConflictError) for r in results) == 1
    winner = next(i for i, r in enumerate(results) if not isinstance(r, Exception))
    assert await value(store) == ['fake-writer-a', 'fake-writer-b'][winner]
    assert await store.get_value_version('TOKEN') == results[winner]


@pytest.mark.asyncio
async def test_concurrent_identical_retries_share_one_receipt(value_store):
    store = value_store
    expected, request_id = await store.get_value_version('TOKEN'), uuid4()
    results = await asyncio.gather(
        *[
            store.replace_shared_value('TOKEN', 'fake-new', expected, request_id)
            for _ in range(3)
        ]
    )
    assert len(set(results)) == 1
    row = await store.get_shared('TOKEN')
    ciphertext = row.secret_value
    assert (
        await store.replace_shared_value('TOKEN', 'fake-new', expected, request_id)
        == results[0]
    )
    assert (await store.get_shared('TOKEN')).secret_value == ciphertext


@pytest.mark.asyncio
async def test_retry_after_a_newer_replacement_does_not_revert_it(value_store):
    store = value_store
    first_expected, first_id = await store.get_value_version('TOKEN'), uuid4()
    first_version = await store.replace_shared_value(
        'TOKEN', 'fake-first', first_expected, first_id
    )
    second_version = await store.replace_shared_value(
        'TOKEN', 'fake-second', first_version, uuid4()
    )
    with pytest.raises(OrgSecretConflictError):
        await store.replace_shared_value(
            'TOKEN', 'fake-first', first_expected, first_id
        )
    assert await value(store) == 'fake-second'
    assert await store.get_value_version('TOKEN') == second_version


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['value', 'expected-version'])
async def test_same_request_id_cannot_change_latest_request(value_store, changed):
    store = value_store
    expected, request_id = await store.get_value_version('TOKEN'), uuid4()
    revision = await store.replace_shared_value(
        'TOKEN', 'fake-π-value', expected, request_id
    )
    with pytest.raises(OrgSecretConflictError):
        await store.replace_shared_value(
            'TOKEN',
            'fake-different' if changed == 'value' else 'fake-π-value',
            expected if changed == 'value' else revision,
            request_id,
        )
    assert await value(store) == 'fake-π-value'
    assert await store.get_value_version('TOKEN') == revision


@pytest.mark.asyncio
async def test_encryption_failure_rolls_back_without_a_gap(value_store, monkeypatch):
    store = value_store
    expected = await store.get_value_version('TOKEN')

    def fail(value):
        raise RuntimeError('Synthetic encryption failure')

    monkeypatch.setattr(store._jwt_svc, 'encrypt_value', fail)
    with pytest.raises(RuntimeError, match='Synthetic encryption failure'):
        await store.replace_shared_value('TOKEN', 'fake-new', expected, uuid4())
    assert await value(store) == 'fake-old'
    assert await store.get_value_version('TOKEN') == expected
    assert (await store.get_shared('TOKEN')).last_replacement_id is None


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_value_and_receipt(
    value_store, async_session_maker
):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    store = value_store
    expected = await store.get_value_version('TOKEN')

    def fail_commit(session):
        raise RuntimeError('Synthetic commit failure')

    event.listen(Session, 'before_commit', fail_commit)
    try:
        with pytest.raises(RuntimeError, match='Synthetic commit failure'):
            await store.replace_shared_value('TOKEN', 'fake-new', expected, uuid4())
    finally:
        event.remove(Session, 'before_commit', fail_commit)
    assert await value(store) == 'fake-old'
    assert await store.get_value_version('TOKEN') == expected
    assert (await store.get_shared('TOKEN')).last_replacement_id is None


async def wait_for_blocked_writers(session_maker, count):
    """Wait for real PG lock contention rather than assuming task scheduling."""

    async def wait():
        while True:
            async with session_maker() as session:
                result = await session.execute(
                    text("""
                    SELECT count(*) FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND wait_event_type = 'Lock'
                      AND query ILIKE '%custom_secrets%'
                """)
                )
                if result.scalar_one() >= count:
                    return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout=10)


@pytest.mark.asyncio
async def test_metadata_edit_cannot_write_back_an_old_value(
    value_store, async_session_maker
):
    store = value_store
    expected = await store.get_value_version('TOKEN')
    tasks = []
    try:
        async with async_session_maker() as blocker:
            await blocker.execute(
                select(StoredCustomSecrets)
                .where(
                    StoredCustomSecrets.org_id == store.org_id,
                    StoredCustomSecrets.secret_name == 'TOKEN',
                )
                .with_for_update()
            )
            replacement = asyncio.create_task(
                store.replace_shared_value('TOKEN', 'fake-new', expected, uuid4())
            )
            tasks.append(replacement)
            await wait_for_blocked_writers(async_session_maker, 1)
            metadata = asyncio.create_task(
                store.update_shared('TOKEN', description='new metadata')
            )
            tasks.append(metadata)
            await wait_for_blocked_writers(async_session_maker, 2)
            # Readers remain available while both actual writers are blocked.
            for _ in range(3):
                assert await value(store) == 'fake-old'
            await blocker.commit()
        revision, _ = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        assert await value(store) == 'fake-new'
        assert await store.get_value_version('TOKEN') == revision
        row = await store.get_shared('TOKEN')
        assert store._jwt_svc.decrypt_value(row.description) == 'new metadata'
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_deleted_and_recreated_secret_rejects_old_operation(value_store):
    store = value_store
    expected = await store.get_value_version('TOKEN')
    old_id = (await store.get_shared('TOKEN')).id
    await store.delete_shared('TOKEN')
    with pytest.raises(OrgSecretNotFoundError):
        await store.replace_shared_value('TOKEN', 'fake-new', expected, uuid4())
    await store.create_shared('TOKEN', 'fake-recreated', None, 'new-owner')
    with pytest.raises(OrgSecretConflictError):
        await store.replace_shared_value('TOKEN', 'fake-new', expected, uuid4())
    assert (await store.get_shared('TOKEN')).id != old_id
    assert await value(store) == 'fake-recreated'


@pytest.mark.asyncio
async def test_other_org_and_personal_rows_are_not_replacement_targets(
    value_store, async_session_maker, create_org
):
    store = value_store
    other = OrgSecretsStore(create_org().id, store._jwt_svc)
    with pytest.raises(OrgSecretNotFoundError):
        await other.get_value_version('TOKEN')
    with pytest.raises(OrgSecretNotFoundError):
        await other.replace_shared_value('TOKEN', 'fake-new', uuid4(), uuid4())
    async with async_session_maker() as session:
        session.add(
            StoredCustomSecrets(
                org_id=other.org_id,
                keycloak_user_id='owner',
                secret_name='PERSONAL',
                secret_value='fake-encrypted-personal',
                is_org_shared=False,
            )
        )
        await session.commit()
    with pytest.raises(OrgSecretNotFoundError):
        await other.replace_shared_value('PERSONAL', 'fake-new', uuid4(), uuid4())
    assert await value(store) == 'fake-old'


@pytest.mark.asyncio
async def test_legacy_duplicate_names_fail_closed(value_store, async_session_maker):
    store = value_store
    expected = await store.get_value_version('TOKEN')
    async with async_session_maker() as session:
        session.add(
            StoredCustomSecrets(
                org_id=store.org_id,
                secret_name='TOKEN',
                secret_value=store._jwt_svc.encrypt_value('fake-duplicate'),
                is_org_shared=True,
            )
        )
        await session.commit()
    with pytest.raises(OrgSecretConflictError):
        await store.get_value_version('TOKEN')
    with pytest.raises(OrgSecretConflictError):
        await store.replace_shared_value('TOKEN', 'fake-new', expected, uuid4())


def test_migration_backfills_revisions_without_changing_ciphertext(
    engine, create_org, monkeypatch
):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = importlib.import_module(
        'migrations.versions.193_add_org_secret_value_versions'
    )
    org = create_org()
    with engine.begin() as connection:
        monkeypatch.setattr(
            migration, 'op', Operations(MigrationContext.configure(connection))
        )
        migration.downgrade()
        for name in ['LEGACY_A', 'LEGACY_B']:
            connection.execute(
                text(
                    'INSERT INTO custom_secrets(org_id, secret_name, secret_value, is_org_shared) VALUES (:org, :name, :value, true)'
                ),
                {'org': org.id, 'name': name, 'value': 'fake-ciphertext'},
            )
        migration.upgrade()
        rows = connection.execute(
            text(
                'SELECT secret_value, value_version, last_replacement_id FROM custom_secrets ORDER BY id'
            )
        ).all()
        assert all(row.secret_value == 'fake-ciphertext' for row in rows)
        assert len({row.value_version for row in rows}) == 2
        assert all(row.last_replacement_id is None for row in rows)
        # A writer from the prior release can still insert without new fields.
        version = connection.execute(
            text(
                "INSERT INTO custom_secrets(org_id, secret_name, secret_value, is_org_shared) VALUES (:org, 'OLD_WRITER', 'fake-ciphertext', true) RETURNING value_version"
            ),
            {'org': org.id},
        ).scalar_one()
        assert version not in {row.value_version for row in rows}
        before_rollback = connection.execute(
            text('SELECT id, value_version FROM custom_secrets')
        ).all()
        connection.execute(
            text(
                'UPDATE custom_secrets SET last_replacement_id = :request, last_replacement_expected_version = :expected'
            ),
            {'request': uuid4(), 'expected': uuid4()},
        )
        migration.downgrade()
        migration.upgrade()
        after_rollback = connection.execute(
            text(
                'SELECT id, value_version, secret_value, last_replacement_id FROM custom_secrets'
            )
        ).all()
        old_versions = {row.id: row.value_version for row in before_rollback}
        assert all(row.value_version != old_versions[row.id] for row in after_rollback)
        assert all(
            row.last_replacement_id is None and row.secret_value == 'fake-ciphertext'
            for row in after_rollback
        )


@pytest.mark.asyncio
async def test_rename_preserves_revision_and_latest_receipt(value_store):
    store = value_store
    expected, request_id = await store.get_value_version('TOKEN'), uuid4()
    revision = await store.replace_shared_value('TOKEN', '', expected, request_id)
    await store.update_shared('TOKEN', new_name='RENAMED', description='renamed')
    assert await store.get_value_version('RENAMED') == revision
    assert (
        await store.replace_shared_value('RENAMED', '', expected, request_id)
        == revision
    )
    assert (await store.load_shared_values())['RENAMED'].secret.get_secret_value() == ''


@pytest.mark.asyncio
async def test_rename_while_replacement_waits_does_not_retarget_request(
    value_store, async_session_maker
):
    store = value_store
    expected = await store.get_value_version('TOKEN')
    task = None
    try:
        async with async_session_maker() as session:
            row = (
                await session.execute(
                    select(StoredCustomSecrets)
                    .where(
                        StoredCustomSecrets.org_id == store.org_id,
                        StoredCustomSecrets.secret_name == 'TOKEN',
                    )
                    .with_for_update()
                )
            ).scalar_one()
            task = asyncio.create_task(
                store.replace_shared_value('TOKEN', 'fake-new', expected, uuid4())
            )
            await wait_for_blocked_writers(async_session_maker, 1)
            row.secret_name = 'RENAMED'
            await session.commit()
        with pytest.raises(OrgSecretNotFoundError):
            await asyncio.wait_for(task, timeout=10)
        assert await store.get_value_version('RENAMED') == expected
        assert (await store.load_shared_values())[
            'RENAMED'
        ].secret.get_secret_value() == 'fake-old'
    finally:
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_concurrent_ambiguous_writers_both_fail_closed(
    value_store, async_session_maker
):
    store = value_store
    expected = await store.get_value_version('TOKEN')
    async with async_session_maker() as session:
        session.add(
            StoredCustomSecrets(
                org_id=store.org_id,
                secret_name='TOKEN',
                secret_value=store._jwt_svc.encrypt_value('fake-duplicate'),
                is_org_shared=True,
            )
        )
        await session.commit()
    results = await asyncio.wait_for(
        asyncio.gather(
            store.replace_shared_value('TOKEN', 'fake-new', expected, uuid4()),
            store.update_shared('TOKEN', description='must not change'),
            return_exceptions=True,
        ),
        timeout=10,
    )
    assert all(isinstance(result, OrgSecretConflictError) for result in results)
    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(StoredCustomSecrets).where(
                        StoredCustomSecrets.org_id == store.org_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {store._jwt_svc.decrypt_value(row.secret_value) for row in rows} == {
            'fake-old',
            'fake-duplicate',
        }
