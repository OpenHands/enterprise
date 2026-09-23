"""Regression tests for ``SaasSecretsStore`` (OHE-3342).

The org-scoped-secrets feature (PR #449) made ``load()`` merge org-shared
secrets into ``custom_secrets``. The V1 custom-secrets write endpoints used
``load()`` then ``store()``, and ``store()`` re-inserted *every* entry in
``custom_secrets`` — including the merged org-shared ones — as spurious
*personal* rows. Every personal-secret save therefore duplicated the org's
shared secrets, and those duplicates had ``is_org_shared=False`` so any member
could edit/delete them via the permission-less V1 endpoints.

These tests exercise the SaaS store against a real PostgreSQL database
(cloned per test via the shared fixtures) and assert the write path no longer
spawns org-shared duplicates.
"""

from uuid import UUID

import pytest
from pydantic import SecretStr

from openhands.app_server.secrets.secrets_models import (
    CustomSecret,
    Secrets,
)
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from storage.saas_secrets_store import SaasSecretsStore
from storage.stored_custom_secrets import StoredCustomSecrets


def _make_jwt_service() -> JwtService:
    key = EncryptionKey(kid='test', key=SecretStr('test_secret'), active=True)
    return JwtService(keys=[key])


@pytest.fixture
def jwt_svc():
    return _make_jwt_service()


@pytest.fixture
def saas_store(async_session_maker, jwt_svc, create_org, create_user, monkeypatch):
    """A ``SaasSecretsStore`` pinned to a real org + user.

    ``a_session_maker`` and ``UserStore.get_user_by_id`` are monkeypatched so
    the store talks to the test database without Keycloak/migration side
    effects. The store is constructed with an explicit ``effective_org_id`` so
    the write path resolves the org without touching ``UserStore``.
    """
    import storage.saas_secrets_store as store_module

    store_module.a_session_maker = async_session_maker

    org = create_org(id=UUID('c3333333-3333-3333-3333-333333333333'))
    user = create_user(current_org_id=org.id)

    async def _fake_get_user_by_id(user_id: str):
        # Minimal stand-in: only ``current_org_id`` is read by the store.
        class _U:
            current_org_id = org.id

        return _U()

    monkeypatch.setattr(
        'storage.saas_secrets_store.UserStore.get_user_by_id',
        staticmethod(_fake_get_user_by_id),
    )

    store = SaasSecretsStore(
        user_id=str(user.id),
        _jwt_svc=jwt_svc,
        effective_org_id=org.id,
    )
    store.org_id = org.id  # convenience for test assertions
    return store


async def _count_rows(store: SaasSecretsStore, *, org_shared: bool) -> int:
    import storage.saas_secrets_store as store_module

    async with store_module.a_session_maker() as session:
        from sqlalchemy import func, select

        stmt = (
            select(func.count())
            .select_from(StoredCustomSecrets)
            .filter(
                StoredCustomSecrets.org_id == store.org_id,
                StoredCustomSecrets.is_org_shared.is_(org_shared),
            )
        )
        result = await session.execute(stmt)
        return int(result.scalar() or 0)


async def _personal_rows(store: SaasSecretsStore) -> list[StoredCustomSecrets]:
    import storage.saas_secrets_store as store_module

    async with store_module.a_session_maker() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(StoredCustomSecrets).filter(
                StoredCustomSecrets.org_id == store.org_id,
                StoredCustomSecrets.is_org_shared.is_(False),
            )
        )
        return list(result.scalars().all())


async def _seed_org_shared(
    store: SaasSecretsStore, name: str, value: str = 'shared-val'
) -> None:
    """Insert an org-shared secret row directly."""
    import storage.saas_secrets_store as store_module

    async with store_module.a_session_maker() as session:
        session.add(
            StoredCustomSecrets(
                keycloak_user_id='admin-user',
                org_id=store.org_id,
                secret_name=name,
                secret_value=store._jwt_svc.encrypt_value(value),
                description=store._jwt_svc.encrypt_value('shared'),
                is_org_shared=True,
            )
        )
        await session.commit()


class TestStoreDoesNotDuplicateOrgSharedSecrets:
    @pytest.mark.asyncio
    async def test_load_personal_never_includes_org_shared(self, saas_store):
        """The write path's load returns only personal secrets, so ``store()``
        is never handed a dict containing org-shared secrets. This is the
        mechanism that prevents OHE-3342 — no defensive name-skipping needed,
        and a personal secret MAY legitimately share a name with an org-shared
        one (the runtime dedups the org-shared one with a ``_2`` suffix)."""
        await _seed_org_shared(saas_store, 'SHARED_KEY', value='shared-val')
        await saas_store.store(
            Secrets(
                custom_secrets={  # type: ignore[arg-type]
                    'SHARED_KEY': CustomSecret(
                        secret=SecretStr('personal-val'), description='mine'
                    ),
                    'MY_PERSONAL': CustomSecret(
                        secret=SecretStr('personal'), description='mine'
                    ),
                }
            )
        )

        # load_personal returns only the two personal secrets, by their bare
        # names — the org-shared SHARED_KEY is absent.
        loaded = await saas_store.load_personal()
        assert set(loaded.custom_secrets.keys()) == {
            'SHARED_KEY',
            'MY_PERSONAL',
        }
        assert (
            loaded.custom_secrets['SHARED_KEY'].secret.get_secret_value()
            == 'personal-val'
        )

    @pytest.mark.asyncio
    async def test_create_personal_does_not_spawn_org_duplicates(self, saas_store):
        """Creating a personal secret via store() must not duplicate org-shared
        secrets. This is the core OHE-3342 repro."""
        await _seed_org_shared(saas_store, 'ORG_TOKEN', value='org-secret')
        await _seed_org_shared(saas_store, 'ORG_OTHER', value='org-secret-2')

        secrets = Secrets(
            custom_secrets={  # type: ignore[arg-type]
                'PERSONAL_KEY': CustomSecret(
                    secret=SecretStr('my-val'), description='mine'
                )
            }
        )
        await saas_store.store(secrets)

        assert await _count_rows(saas_store, org_shared=True) == 2
        # Exactly one personal row — the one we just wrote.
        assert await _count_rows(saas_store, org_shared=False) == 1
        personal = await _personal_rows(saas_store)
        assert {r.secret_name for r in personal} == {'PERSONAL_KEY'}

    @pytest.mark.asyncio
    async def test_repeated_writes_do_not_grow_personal_rows(self, saas_store):
        """Each subsequent personal write must not accumulate duplicates of
        org-shared secrets.

        ``store()`` is a whole-replacement of the user's personal secrets, so
        each call passes the full intended set (as the V1 endpoints do after
        load-mutate-store). Across iterations the personal set grows by one
        each time, but the org-shared rows stay fixed and no personal
        duplicates of the org-shared secret appear.
        """
        await _seed_org_shared(saas_store, 'ORG_TOKEN', value='org-secret')

        personal_names: list[str] = []
        for i in range(3):
            personal_names.append(f'PERSONAL_{i}')
            await saas_store.store(
                Secrets(
                    custom_secrets={  # type: ignore[arg-type]
                        name: CustomSecret(secret=SecretStr(name), description='')
                        for name in personal_names
                    }
                )
            )

        # Org-shared rows are untouched.
        assert await _count_rows(saas_store, org_shared=True) == 1
        # Personal rows are exactly the three we wrote — no duplicates.
        personal = await _personal_rows(saas_store)
        assert sorted(r.secret_name for r in personal) == [
            'PERSONAL_0',
            'PERSONAL_1',
            'PERSONAL_2',
        ]

    @pytest.mark.asyncio
    async def test_db_constraint_prevents_duplicate_personal_rows(self, saas_store):
        """The partial unique index (migration 165) is the defense-in-depth:
        even if two personal rows with the same (user, org, name) were ever
        inserted, the DB rejects the second one."""
        from sqlalchemy.exc import IntegrityError

        import storage.saas_secrets_store as store_module

        async with store_module.a_session_maker() as session:
            session.add(
                StoredCustomSecrets(
                    keycloak_user_id=saas_store.user_id,
                    org_id=saas_store.org_id,
                    secret_name='DUP_KEY',
                    secret_value=saas_store._jwt_svc.encrypt_value('v1'),
                    description=None,
                    is_org_shared=False,
                )
            )
            await session.commit()

        with pytest.raises(IntegrityError):
            async with store_module.a_session_maker() as session:
                session.add(
                    StoredCustomSecrets(
                        keycloak_user_id=saas_store.user_id,
                        org_id=saas_store.org_id,
                        secret_name='DUP_KEY',
                        secret_value=saas_store._jwt_svc.encrypt_value('v2'),
                        description=None,
                        is_org_shared=False,
                    )
                )
                await session.commit()


class TestLoadPersonalExcludesOrgShared:
    @pytest.mark.asyncio
    async def test_load_personal_returns_only_personal(self, saas_store):
        """``load_personal`` returns personal secrets verbatim (no merge, no
        suffix dedup) and never includes org-shared secrets."""
        await _seed_org_shared(saas_store, 'ORG_TOKEN', value='org-secret')

        # Seed a personal secret.
        await saas_store.store(
            Secrets(
                custom_secrets={  # type: ignore[arg-type]
                    'PERSONAL_KEY': CustomSecret(
                        secret=SecretStr('my-val'), description='mine'
                    )
                }
            )
        )

        loaded = await saas_store.load_personal()
        assert set(loaded.custom_secrets.keys()) == {'PERSONAL_KEY'}
        assert (
            loaded.custom_secrets['PERSONAL_KEY'].secret.get_secret_value() == 'my-val'
        )

    @pytest.mark.asyncio
    async def test_load_merges_org_shared_for_runtime(self, saas_store, monkeypatch):
        """``load()`` (runtime path) still merges org-shared secrets with
        suffix dedup, so runtime env vars are unchanged by the fix."""
        await _seed_org_shared(saas_store, 'ORG_TOKEN', value='org-secret')

        await saas_store.store(
            Secrets(
                custom_secrets={  # type: ignore[arg-type]
                    'ORG_TOKEN': CustomSecret(
                        secret=SecretStr('personal-wins'), description='mine'
                    )
                }
            )
        )

        loaded = await saas_store.load()
        # Personal keeps the bare name; the org-shared one is suffixed.
        assert 'ORG_TOKEN' in loaded.custom_secrets
        assert (
            loaded.custom_secrets['ORG_TOKEN'].secret.get_secret_value()
            == 'personal-wins'
        )
        assert 'ORG_TOKEN_2' in loaded.custom_secrets
        assert (
            loaded.custom_secrets['ORG_TOKEN_2'].secret.get_secret_value()
            == 'org-secret'
        )
