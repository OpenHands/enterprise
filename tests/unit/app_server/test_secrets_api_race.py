"""Tests for the secrets write lock that prevents lost writes (OHE-3052).

The endpoints in ``secrets_router`` all follow the ``load -> mutate -> store``
shape, which is unsafe under concurrency: two requests that read the same
baseline can each store their own delta and silently drop the other's
changes. The fix is to acquire a per-user write lock around the critical
section so writes serialize.

In OSS mode the lock is a single-process ``asyncio.Lock`` keyed on the user
(the tests below exercise that path). In SaaS mode it switches to a Redis
distributed lock; that's exercised separately by the SaaS-specific tests.
"""

import asyncio
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from openhands.app_server.file_store import get_file_store
from openhands.app_server.integrations.provider import (
    CustomSecret,
    ProviderType,
)
from openhands.app_server.secrets.file_secrets_store import FileSecretsStore
from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.secrets.secrets_router import (
    _reset_user_secrets_locks,
    _secrets_write_lock,
    _secrets_write_lock_key,
    _user_secrets_write_locks,
)
from openhands.app_server.secrets.secrets_router import (
    router as secrets_router,
)


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(secrets_router)
    return app


@pytest.fixture
def temp_dir(tmp_path_factory: pytest.TempPathFactory) -> str:
    return str(tmp_path_factory.mktemp('secrets_store_race'))


@pytest.fixture
def file_secrets_store(temp_dir):
    file_store = get_file_store('local', temp_dir)
    store = FileSecretsStore(file_store)
    with patch(
        'openhands.app_server.secrets.file_secrets_store.FileSecretsStore.get_instance',
        AsyncMock(return_value=store),
    ):
        yield store


@pytest.fixture
async def test_client(app):
    """Use httpx.AsyncClient with the ASGI transport so requests run on
    the same event loop as the test. This is critical for exercising
    the asyncio.Lock: a separate loop would create its own lock object
    and the requests would no longer serialize."""
    with patch.dict(os.environ, {'SESSION_API_KEY': ''}, clear=False):
        with patch('openhands.app_server.utils.dependencies._SESSION_API_KEY', None):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url='http://test'
            ) as client:
                yield client


@pytest.fixture(autouse=True)
def _reset_lock_registry():
    """Locks bind to the event loop that first awaited them; pytest
    spins a fresh loop per test, so any stale Lock carried over from
    a previous test would be attached to a dead loop. Clearing between
    tests fixes it."""
    _reset_user_secrets_locks()
    yield
    _reset_user_secrets_locks()


# ----------------------------------------------------------------------
# Pure helper tests
# ----------------------------------------------------------------------


def test_secrets_write_lock_key_uses_user_and_org():
    """Lock key must include both user_id and the effective org_id so
    different (user, org) scopes don't serialize against each other."""
    org_id = uuid4()
    store = FileSecretsStore(get_file_store('local', '/tmp'))
    # The FileSecretsStore doesn't expose effective_org_id; in SaasSecretsStore
    # it does. We attach the attribute to simulate the SaaS case.
    store.effective_org_id = org_id
    assert (
        _secrets_write_lock_key('alice', store) == f'app_secrets:write:alice:{org_id}'
    )


def test_secrets_write_lock_key_falls_back_when_no_org():
    """FileSecretsStore has no effective_org_id; the key must still be
    stable per user rather than collapsing multiple users to one lock."""
    store = FileSecretsStore(get_file_store('local', '/tmp'))
    assert _secrets_write_lock_key('alice', store) == 'app_secrets:write:alice:default'
    assert _secrets_write_lock_key('bob', store) == 'app_secrets:write:bob:default'
    assert _secrets_write_lock_key('alice', store) != _secrets_write_lock_key(
        'bob', store
    )


def test_secrets_write_lock_key_handles_anonymous_user():
    store = FileSecretsStore(get_file_store('local', '/tmp'))
    assert (
        _secrets_write_lock_key(None, store) == 'app_secrets:write:<anonymous>:default'
    )


@pytest.mark.asyncio
async def test_secrets_write_lock_serializes_in_process():
    """OSS path: the asyncio.Lock must actually serialize concurrent
    critical sections for the same key."""
    store = FileSecretsStore(get_file_store('local', '/tmp'))
    entered: list[str] = []
    second_started = asyncio.Event()

    @asynccontextmanager
    async def use_lock():
        async with _secrets_write_lock('alice', store):
            entered.append('enter')
            yield
            entered.append('exit')

    async def first():
        async with use_lock():
            # Stay inside the critical section until the second task
            # has had a chance to attempt to acquire.
            await second_started.wait()
            entered.append('first_exit')

    async def second():
        # Wait until the first task is inside the lock.
        while 'enter' not in entered:
            await asyncio.sleep(0)
        second_started.set()
        async with use_lock():
            entered.append('second_enter')

    await asyncio.gather(first(), second())

    # The second task must not have entered the critical section until
    # the first task finished.
    idx_first_exit = entered.index('first_exit')
    assert entered.index('second_enter') > idx_first_exit
    # The interleaving must look like: first finished before second entered.
    first_segment = entered[: entered.index('enter', 1)]
    assert first_segment == ['enter', 'first_exit', 'exit'], first_segment
    assert entered[entered.index('enter', 1) :] == [
        'enter',
        'second_enter',
        'exit',
    ]


@pytest.mark.asyncio
async def test_secrets_write_lock_does_not_serialize_different_user():
    """Locks for different users are independent so two users can write
    in parallel."""
    store = FileSecretsStore(get_file_store('local', '/tmp'))
    order: list[str] = []
    hold = asyncio.Event()
    release = asyncio.Event()

    async def worker(user_id: str):
        async with _secrets_write_lock(user_id, store):
            order.append(f'{user_id}:enter')
            if not hold.is_set():
                hold.set()
            await release.wait()
            order.append(f'{user_id}:exit')

    t1 = asyncio.create_task(worker('alice'))
    await hold.wait()
    t2 = asyncio.create_task(worker('bob'))
    # Give t2 a chance to enter its lock.
    await asyncio.sleep(0)
    # Both should now be inside their respective critical sections.
    assert 'alice:enter' in order
    assert 'bob:enter' in order
    release.set()
    await asyncio.gather(t1, t2)


@pytest.mark.asyncio
async def test_secrets_write_lock_released_on_exception():
    """If the critical section raises, the lock must still be released
    so subsequent calls aren't blocked forever."""
    store = FileSecretsStore(get_file_store('local', '/tmp'))

    with pytest.raises(RuntimeError):
        async with _secrets_write_lock('alice', store):
            raise RuntimeError('boom')

    # Second acquire should not block.
    async with _secrets_write_lock('alice', store):
        pass


# ----------------------------------------------------------------------
# End-to-end concurrency tests using the real FastAPI router
# ----------------------------------------------------------------------
#
# These tests prove OHE-3052 is fixed by replicating the original race
# window: each request reads the baseline, mutates locally, and writes
# back. Without the lock, two requests reading the same baseline both
# write their own delta and the second one clobbers the first. With
# the lock, the requests serialize and no update is lost.
#
# Determinism: we inject a small ``await asyncio.sleep(0)`` between load
# and store so that any racing request gets a chance to also enter its
# load step. The lock then prevents that racing request from continuing
# until the first one finishes its store. Without the lock, the racing
# request's store would clobber the first.


def _install_race_window(file_secrets_store: FileSecretsStore) -> None:
    """Insert a single ``await asyncio.sleep(0)`` between load and store
    so that any other concurrent request has a chance to enter its own
    load before the original request stores. Without the write lock, two
    such requests would both observe the same baseline and the second
    store would silently clobber the first."""
    real_load = file_secrets_store.load
    real_store = file_secrets_store.store

    async def racing_load():
        result = await real_load()
        # Yield to the scheduler so any other racing request can enter
        # its own load() before we proceed to store().
        await asyncio.sleep(0)
        return result

    file_secrets_store.load = racing_load
    file_secrets_store.store = real_store


@pytest.mark.asyncio
async def test_concurrent_create_custom_secrets_do_not_drop_writes(
    test_client, file_secrets_store
):
    """Regression test for OHE-3052: N concurrent creates for distinct
    secret names must end up with all N secrets stored. Without per-user
    write locking, the second request reads the same baseline as the
    first and overwrites the first request's secret."""
    await file_secrets_store.store(Secrets())
    _install_race_window(file_secrets_store)

    secret_names = [f'SECRET_{i:02d}' for i in range(20)]

    async def post_one(name: str):
        return await test_client.post(
            '/secrets',
            json={'name': name, 'value': f'value-{name}', 'description': None},
        )

    results = await asyncio.gather(*(post_one(n) for n in secret_names))
    assert all(r.status_code == 201 for r in results), [r.status_code for r in results]

    stored = await file_secrets_store.load()
    assert set(stored.custom_secrets.keys()) == set(secret_names)


@pytest.mark.asyncio
async def test_concurrent_create_and_update_preserve_both(
    test_client, file_secrets_store
):
    """A create racing with an update must not drop the secret being
    updated. Without the lock, both requests read the seeded state, the
    update changes only OLD_NAME's description, then the create stores
    a Secrets with only NEW_SECRET, dropping OLD_NAME."""
    original = CustomSecret(secret=SecretStr('original-value'))
    await file_secrets_store.store(Secrets(custom_secrets={'OLD_NAME': original}))  # type: ignore[arg-type]
    _install_race_window(file_secrets_store)

    async def post_create():
        return await test_client.post(
            '/secrets',
            json={'name': 'NEW_SECRET', 'value': 'new-value', 'description': None},
        )

    async def put_update():
        return await test_client.put(
            '/secrets/OLD_NAME',
            json={'name': 'OLD_NAME', 'description': 'updated'},
        )

    results = await asyncio.gather(post_create(), put_update())
    assert sorted(r.status_code for r in results) == [200, 201]

    stored = await file_secrets_store.load()
    assert set(stored.custom_secrets.keys()) == {'OLD_NAME', 'NEW_SECRET'}
    assert stored.custom_secrets['OLD_NAME'].description == 'updated'
    assert stored.custom_secrets['NEW_SECRET'].secret.get_secret_value() == 'new-value'


@pytest.mark.asyncio
async def test_concurrent_create_and_delete_preserve_created(
    test_client, file_secrets_store
):
    """A delete racing with a create must not wipe the just-created
    secret, and must not resurrect the deleted one. Without the lock,
    the create's load may observe the store before the delete completes
    and then store its full custom_secrets dict, missing the deletion;
    or vice versa."""
    seeded = {
        f'SECRET_{i:02d}': CustomSecret(secret=SecretStr(f'value-{i}'))
        for i in range(5)
    }
    await file_secrets_store.store(Secrets(custom_secrets=seeded))  # type: ignore[arg-type]
    _install_race_window(file_secrets_store)

    async def post_create():
        return await test_client.post(
            '/secrets',
            json={
                'name': 'NEW_SECRET',
                'value': 'new-value',
                'description': None,
            },
        )

    async def delete_one():
        return await test_client.delete('/secrets/SECRET_00')

    results = await asyncio.gather(post_create(), delete_one())
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 201]

    stored = await file_secrets_store.load()
    assert 'NEW_SECRET' in stored.custom_secrets
    assert 'SECRET_00' not in stored.custom_secrets
    # The other seeded secrets should still be there.
    assert set(stored.custom_secrets.keys()) == {
        'SECRET_01',
        'SECRET_02',
        'SECRET_03',
        'SECRET_04',
        'NEW_SECRET',
    }


@pytest.mark.asyncio
async def test_concurrent_git_provider_token_updates_preserve_tokens(
    test_client, file_secrets_store
):
    """Two concurrent POSTs to /git-providers for the same provider
    must not lose the token. Without the lock, both requests read the
    empty store, and the second store can overwrite the first if the
    second request's load saw stale state.

    To focus on the lock (rather than the provider_tokens merge logic,
    which is a separate concern), both requests target the same
    provider. The lock ensures the second request reads what the
    first one wrote, so the final token is whichever request ran
    last.
    """
    await file_secrets_store.store(Secrets())
    _install_race_window(file_secrets_store)

    async def post_github(initial_token: str):
        with patch(
            'openhands.app_server.secrets.secrets_router.check_provider_tokens',
            AsyncMock(return_value=''),
        ):
            return await test_client.post(
                '/secrets/git-providers',
                json={
                    'provider_tokens': {
                        'github': {'token': initial_token, 'host': 'github.com'},
                    }
                },
            )

    results = await asyncio.gather(
        post_github('github-token-a'),
        post_github('github-token-b'),
    )
    assert sorted(r.status_code for r in results) == [200, 200]

    stored = await file_secrets_store.load()
    assert ProviderType.GITHUB in stored.provider_tokens
    token = stored.provider_tokens[ProviderType.GITHUB].token.get_secret_value()
    assert token in {'github-token-a', 'github-token-b'}, token


def test_lock_registry_is_module_scoped():
    """Sanity check: the lock registry is the module-level dict, so
    test isolation via _reset_user_secrets_locks() truly clears state."""
    _user_secrets_write_locks['sentinel'] = asyncio.Lock()
    _reset_user_secrets_locks()
    assert 'sentinel' not in _user_secrets_write_locks
