from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import SecretStr

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '164_backfill_user_settings_managed_llm_key.py'
)
spec = spec_from_file_location('migration_164', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_164 = module_from_spec(spec)
spec.loader.exec_module(migration_164)


def _make_tables(metadata: sa.MetaData):
    org_member = sa.Table(
        'org_member',
        metadata,
        sa.Column('org_id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), primary_key=True),
        sa.Column('_llm_api_key', sa.String(), nullable=False),
    )
    user_settings = sa.Table(
        'user_settings',
        metadata,
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('keycloak_user_id', sa.String()),
        sa.Column('llm_api_key', sa.String()),
    )
    return org_member, user_settings


def _run_upgrade(monkeypatch):
    engine = sa.create_engine('sqlite://')
    metadata = sa.MetaData()
    org_member, user_settings = _make_tables(metadata)
    metadata.create_all(engine)

    jwt_service = JwtService(
        [
            EncryptionKey(
                id='migration-test-key',
                key=SecretStr('migration-test-secret'),
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ]
    )
    import storage.encrypt_utils as encrypt_utils

    monkeypatch.setattr(encrypt_utils, '_jwt_service', jwt_service)
    # get_fernet caches on a module global; reset so it derives from the test key.
    monkeypatch.setattr(encrypt_utils, '_fernet', None)

    return engine, org_member, user_settings, encrypt_utils


def test_upgrade_resyncs_rotated_key_into_user_settings(monkeypatch):
    engine, org_member, user_settings, encrypt_utils = _run_upgrade(monkeypatch)

    user_id = uuid4()
    new_key = 'sk-litellm-new-rotated'
    stale_key = 'sk-litellm-old-deleted'

    with engine.begin() as connection:
        # Personal org: org_id == user_id, carries the freshly rotated key.
        connection.execute(
            org_member.insert().values(
                org_id=user_id,
                user_id=user_id,
                _llm_api_key=encrypt_utils.encrypt_value(new_key),
            )
        )
        # Legacy cache still points at the deleted key.
        connection.execute(
            user_settings.insert().values(
                id=1,
                keycloak_user_id=str(user_id),
                llm_api_key=encrypt_utils.encrypt_legacy_value(stale_key),
            )
        )

        context = MigrationContext.configure(connection)
        monkeypatch.setattr(migration_164, 'op', Operations(context))
        migration_164.upgrade()

        stored = connection.execute(
            sa.select(user_settings.c.llm_api_key).where(
                user_settings.c.keycloak_user_id == str(user_id)
            )
        ).scalar_one()

    assert encrypt_utils.decrypt_legacy_value(stored) == new_key


def test_upgrade_ignores_non_personal_org_members(monkeypatch):
    engine, org_member, user_settings, encrypt_utils = _run_upgrade(monkeypatch)

    user_id = uuid4()
    other_org_id = uuid4()
    org_key = 'sk-litellm-shared-org'
    stale_key = 'sk-litellm-old-deleted'

    with engine.begin() as connection:
        # Membership in a *shared* org (org_id != user_id) must not drive the
        # personal user_settings cache.
        connection.execute(
            org_member.insert().values(
                org_id=other_org_id,
                user_id=user_id,
                _llm_api_key=encrypt_utils.encrypt_value(org_key),
            )
        )
        connection.execute(
            user_settings.insert().values(
                id=1,
                keycloak_user_id=str(user_id),
                llm_api_key=encrypt_utils.encrypt_legacy_value(stale_key),
            )
        )

        context = MigrationContext.configure(connection)
        monkeypatch.setattr(migration_164, 'op', Operations(context))
        migration_164.upgrade()

        stored = connection.execute(
            sa.select(user_settings.c.llm_api_key).where(
                user_settings.c.keycloak_user_id == str(user_id)
            )
        ).scalar_one()

    assert encrypt_utils.decrypt_legacy_value(stored) == stale_key


def test_upgrade_is_idempotent_when_already_in_sync(monkeypatch):
    engine, org_member, user_settings, encrypt_utils = _run_upgrade(monkeypatch)

    user_id = uuid4()
    key = 'sk-litellm-current'

    with engine.begin() as connection:
        connection.execute(
            org_member.insert().values(
                org_id=user_id,
                user_id=user_id,
                _llm_api_key=encrypt_utils.encrypt_value(key),
            )
        )
        already = encrypt_utils.encrypt_legacy_value(key)
        connection.execute(
            user_settings.insert().values(
                id=1,
                keycloak_user_id=str(user_id),
                llm_api_key=already,
            )
        )

        context = MigrationContext.configure(connection)
        monkeypatch.setattr(migration_164, 'op', Operations(context))
        migration_164.upgrade()

        stored = connection.execute(
            sa.select(user_settings.c.llm_api_key).where(
                user_settings.c.keycloak_user_id == str(user_id)
            )
        ).scalar_one()

    # Same plaintext round-trips; ciphertext stays readable as the current key.
    assert encrypt_utils.decrypt_legacy_value(stored) == key


def test_upgrade_skips_when_no_user_settings_row(monkeypatch):
    engine, org_member, user_settings, encrypt_utils = _run_upgrade(monkeypatch)

    user_id = uuid4()

    with engine.begin() as connection:
        connection.execute(
            org_member.insert().values(
                org_id=user_id,
                user_id=user_id,
                _llm_api_key=encrypt_utils.encrypt_value('sk-litellm-new'),
            )
        )

        context = MigrationContext.configure(connection)
        monkeypatch.setattr(migration_164, 'op', Operations(context))
        # Must not raise when the cache row is absent (new sign-ups).
        migration_164.upgrade()

        count = connection.execute(
            sa.select(sa.func.count()).select_from(user_settings)
        ).scalar_one()

    assert count == 0
