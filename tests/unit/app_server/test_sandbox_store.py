"""Tests for the sandbox record every backend shares.

The scoping helper here is what every backend reads ownership through, and
``session_auth.validate_session_key`` and ``webhook_router.valid_sandbox``
depend on its admin case, so the cases below are the authentication contract
rather than incidental query behaviour.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr
from sqlalchemy import select, text

from openhands.app_server.sandbox.sandbox_store import (
    DOCKER_BACKEND,
    E2B_BACKEND,
    REMOTE_BACKEND,
    StoredSandbox,
    get_stored_sandbox,
    get_stored_sandbox_by_session_api_key,
    hash_session_api_key,
    search_stored_sandboxes,
)
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey

OWNER_ID = 'user-a'
OTHER_USER_ID = 'user-b'
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
KEY_A = EncryptionKey(id='key-a', key=SecretStr('key-a-secret'), created_at=CREATED_AT)
KEY_B = EncryptionKey(
    id='key-b',
    key=SecretStr('key-b-secret'),
    created_at=CREATED_AT + timedelta(days=1),
)


def _user_context(user_id: str | None) -> AsyncMock:
    context = AsyncMock()
    context.get_user_id.return_value = user_id
    return context


def _stored(
    sandbox_id: str,
    created_by_user_id: str | None = OWNER_ID,
    backend: str = DOCKER_BACKEND,
    session_api_key: str | None = None,
    created_at: datetime | None = None,
) -> StoredSandbox:
    return StoredSandbox(
        id=sandbox_id,
        backend=backend,
        created_by_user_id=created_by_user_id,
        sandbox_spec_id='spec-1',
        session_api_key_hash=(
            hash_session_api_key(session_api_key) if session_api_key else None
        ),
        created_at=created_at or CREATED_AT,
    )


@contextmanager
def _encryption_keys(*keys: EncryptionKey) -> Iterator[JwtService]:
    """Point ``StoredSecretStr`` at a JWT service holding these keys."""
    jwt_service = JwtService(keys=list(keys))
    config = SimpleNamespace(jwt=SimpleNamespace(get_jwt_service=lambda: jwt_service))
    with patch('openhands.app_server.config.get_global_config', return_value=config):
        yield jwt_service


async def _reload(db_session, sandbox_id: str) -> StoredSandbox:
    """Read a row back from the database rather than the identity map."""
    db_session.expunge_all()
    result = await db_session.execute(
        select(StoredSandbox).where(StoredSandbox.id == sandbox_id)
    )
    return result.scalar_one()


@pytest.fixture
async def db_session(async_session_maker):
    async with async_session_maker() as session:
        yield session


@pytest.fixture
def store(db_session):
    async def _store(*sandboxes: StoredSandbox) -> None:
        for sandbox in sandboxes:
            db_session.add(sandbox)
        await db_session.flush()

    return _store


class TestHashSessionApiKey:
    def test_is_sha256_hex(self):
        assert hash_session_api_key('a-key') == hashlib.sha256(b'a-key').hexdigest()

    def test_is_stable(self):
        assert hash_session_api_key('a-key') == hash_session_api_key('a-key')

    def test_differs_between_keys(self):
        assert hash_session_api_key('a-key') != hash_session_api_key('another-key')


class TestScoping:
    async def test_owner_sees_their_own(self, db_session, store):
        await store(_stored('sb-1'))

        found = await get_stored_sandbox(
            db_session, _user_context(OWNER_ID), DOCKER_BACKEND, 'sb-1'
        )

        assert found is not None
        assert found.id == 'sb-1'

    async def test_owner_cannot_see_another_users(self, db_session, store):
        await store(_stored('sb-1', created_by_user_id=OTHER_USER_ID))

        found = await get_stored_sandbox(
            db_session, _user_context(OWNER_ID), DOCKER_BACKEND, 'sb-1'
        )

        assert found is None

    async def test_caller_without_a_user_id_sees_every_row(self, db_session, store):
        """The admin case webhook and session key auth run under."""
        await store(
            _stored('sb-1', created_by_user_id=OWNER_ID),
            _stored('sb-2', created_by_user_id=OTHER_USER_ID),
        )

        page = await search_stored_sandboxes(
            db_session, _user_context(None), DOCKER_BACKEND, None, 100
        )

        assert {row.id for row in page.items} == {'sb-1', 'sb-2'}

    async def test_backends_do_not_see_each_other(self, db_session, store):
        """A deployment that switches RUNTIME must not mix the backends' rows."""
        await store(
            _stored('sb-remote', backend=REMOTE_BACKEND),
            _stored('sb-docker', backend=DOCKER_BACKEND),
            _stored('sb-e2b', backend=E2B_BACKEND),
        )

        for backend, sandbox_id in [
            (REMOTE_BACKEND, 'sb-remote'),
            (DOCKER_BACKEND, 'sb-docker'),
            (E2B_BACKEND, 'sb-e2b'),
        ]:
            page = await search_stored_sandboxes(
                db_session, _user_context(None), backend, None, 100
            )
            assert [row.id for row in page.items] == [sandbox_id]

    async def test_a_row_written_without_a_backend_is_remote(self, db_session):
        await db_session.execute(
            text(
                'INSERT INTO v1_remote_sandbox (id, sandbox_spec_id) '
                "VALUES ('sb-1', 'spec-1')"
            )
        )

        found = await get_stored_sandbox(
            db_session, _user_context(None), REMOTE_BACKEND, 'sb-1'
        )

        assert found is not None
        assert found.backend == REMOTE_BACKEND


class TestSessionApiKeyLookup:
    async def test_finds_the_sandbox_holding_the_key(self, db_session, store):
        await store(_stored('sb-1', session_api_key='the-key'))

        found = await get_stored_sandbox_by_session_api_key(
            db_session, _user_context(OWNER_ID), DOCKER_BACKEND, 'the-key'
        )

        assert found is not None
        assert found.id == 'sb-1'

    async def test_a_wrong_key_finds_nothing(self, db_session, store):
        await store(_stored('sb-1', session_api_key='the-key'))

        found = await get_stored_sandbox_by_session_api_key(
            db_session, _user_context(OWNER_ID), DOCKER_BACKEND, 'not-the-key'
        )

        assert found is None

    async def test_a_leaked_key_stays_scoped_to_its_owner(self, db_session, store):
        await store(
            _stored('sb-1', created_by_user_id=OTHER_USER_ID, session_api_key='the-key')
        )

        found = await get_stored_sandbox_by_session_api_key(
            db_session, _user_context(OWNER_ID), DOCKER_BACKEND, 'the-key'
        )

        assert found is None


class TestStoredSessionApiKey:
    """The key column is encrypted with the app's keys and decrypted on read."""

    @staticmethod
    def _with_key(sandbox_id: str, session_api_key: str) -> StoredSandbox:
        sandbox = _stored(
            sandbox_id, backend=E2B_BACKEND, session_api_key=session_api_key
        )
        sandbox.session_api_key = SecretStr(session_api_key)
        return sandbox

    async def test_the_key_reads_back_decrypted(self, db_session, store):
        with _encryption_keys(KEY_A):
            await store(self._with_key('sb-1', 'the-key'))
            stored = (
                await db_session.execute(
                    text(
                        "SELECT session_api_key FROM v1_remote_sandbox WHERE id = 'sb-1'"
                    )
                )
            ).scalar_one()
            row = await _reload(db_session, 'sb-1')

        assert stored != 'the-key'
        assert row.session_api_key is not None
        assert row.session_api_key.get_secret_value() == 'the-key'

    async def test_a_new_default_key_still_reads_existing_rows(self, db_session, store):
        """Adding an encryption key must not orphan the keys already stored."""
        with _encryption_keys(KEY_A):
            await store(self._with_key('sb-1', 'the-key'))

        with _encryption_keys(KEY_A, KEY_B) as jwt_service:
            assert jwt_service.default_key_id == KEY_B.id
            row = await _reload(db_session, 'sb-1')

        assert row.session_api_key is not None
        assert row.session_api_key.get_secret_value() == 'the-key'

    async def test_a_row_without_a_key_reads_back_none(self, db_session, store):
        await store(_stored('sb-1'))

        row = await _reload(db_session, 'sb-1')

        assert row.session_api_key is None


class TestSearch:
    async def test_newest_first(self, db_session, store):
        await store(
            _stored('sb-old', created_at=CREATED_AT),
            _stored('sb-new', created_at=CREATED_AT + timedelta(days=1)),
        )

        page = await search_stored_sandboxes(
            db_session, _user_context(OWNER_ID), DOCKER_BACKEND, None, 100
        )

        assert [row.id for row in page.items] == ['sb-new', 'sb-old']
        assert page.next_page_id is None

    async def test_pages_on_an_offset(self, db_session, store):
        await store(
            *[
                _stored(f'sb-{index}', created_at=CREATED_AT + timedelta(days=index))
                for index in range(5)
            ]
        )
        user_context = _user_context(OWNER_ID)

        first = await search_stored_sandboxes(
            db_session, user_context, DOCKER_BACKEND, None, 3
        )
        assert [row.id for row in first.items] == ['sb-4', 'sb-3', 'sb-2']
        assert first.next_page_id == '3'

        second = await search_stored_sandboxes(
            db_session, user_context, DOCKER_BACKEND, first.next_page_id, 3
        )
        assert [row.id for row in second.items] == ['sb-1', 'sb-0']
        assert second.next_page_id is None

    async def test_an_unparseable_page_id_starts_from_the_beginning(
        self, db_session, store
    ):
        """`page_iterator` must terminate, so a bad cursor cannot loop."""
        await store(_stored('sb-1'))

        page = await search_stored_sandboxes(
            db_session, _user_context(OWNER_ID), DOCKER_BACKEND, 'not-a-number', 100
        )

        assert [row.id for row in page.items] == ['sb-1']
        assert page.next_page_id is None
