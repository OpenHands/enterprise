import importlib.util
from datetime import UTC, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, delete, event, insert, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from storage.auth_action_tokens import AuthActionToken
from storage.auth_sessions import AuthSession
from storage.external_identities import ExternalIdentity
from storage.installation_auth import InstallationAuth
from storage.local_credentials import LocalCredentials, normalize_login_email
from storage.org import Org
from storage.user import User
from tests.unit.auth_schema import create_auth_schema


@pytest.fixture
def auth_db():
    engine = create_engine('sqlite:///:memory:')

    @event.listens_for(engine, 'connect')
    def enable_foreign_keys(connection, _record):
        connection.execute('PRAGMA foreign_keys=ON')

    create_auth_schema(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def add_user(session, email='same@example.com'):
    user_id = uuid4()
    session.execute(insert(Org).values(id=user_id, name=str(user_id)))
    session.execute(
        insert(User).values(id=user_id, current_org_id=user_id, email=email)
    )
    return user_id


def test_local_login_unique_without_rejecting_legacy_duplicate_emails(auth_db):
    first = add_user(auth_db)
    second = add_user(auth_db)
    auth_db.add(
        LocalCredentials(
            user_id=first,
            normalized_email=normalize_login_email(' Same@Example.com '),
            password_hash='hash',
        )
    )
    auth_db.commit()
    auth_db.add(
        LocalCredentials(
            user_id=second, normalized_email='same@example.com', password_hash='hash'
        )
    )
    with pytest.raises(IntegrityError):
        auth_db.commit()


def test_local_credential_only_one_per_user(auth_db):
    user_id = add_user(auth_db)
    auth_db.add(
        LocalCredentials(
            user_id=user_id, normalized_email='a@example.com', password_hash='hash'
        )
    )
    auth_db.commit()
    auth_db.add(
        LocalCredentials(
            user_id=user_id, normalized_email='b@example.com', password_hash='hash'
        )
    )
    with pytest.raises(IntegrityError):
        auth_db.commit()


def test_local_email_normalization_preserves_plus_and_dots():
    assert (
        normalize_login_email('  First.Last+Tag@Example.COM ')
        == 'first.last+tag@example.com'
    )


def test_unnormalized_email_is_rejected(auth_db):
    user_id = add_user(auth_db)
    auth_db.add(
        LocalCredentials(
            user_id=user_id,
            normalized_email=' Mixed@Example.com ',
            password_hash='hash',
        )
    )
    with pytest.raises(IntegrityError):
        auth_db.commit()


def test_external_identity_uniqueness_is_issuer_subject_not_email(auth_db):
    first = add_user(auth_db)
    second = add_user(auth_db)
    auth_db.add_all(
        [
            ExternalIdentity(
                user_id=first,
                connection='keycloak',
                issuer='issuer-one',
                subject='subject',
            ),
            ExternalIdentity(
                user_id=second,
                connection='keycloak',
                issuer='issuer-two',
                subject='subject',
            ),
        ]
    )
    auth_db.commit()
    auth_db.add(
        ExternalIdentity(
            user_id=second,
            connection='keycloak',
            issuer='issuer-one',
            subject='subject',
        )
    )
    with pytest.raises(IntegrityError):
        auth_db.commit()


def test_delete_account_cascades_secrets_but_preserves_bootstrap_history(auth_db):
    user_id = add_user(auth_db)
    expiry = datetime.now(UTC) + timedelta(hours=1)
    auth_db.add_all(
        [
            LocalCredentials(
                user_id=user_id,
                normalized_email='same@example.com',
                password_hash='hash',
            ),
            ExternalIdentity(
                user_id=user_id,
                connection='connection',
                issuer='issuer',
                subject='subject',
            ),
            AuthSession(token_digest='a' * 64, user_id=user_id, expires_at=expiry),
            AuthActionToken(
                token_digest='b' * 64,
                user_id=user_id,
                purpose='password_reset',
                expires_at=expiry,
            ),
            InstallationAuth(
                mode='local', bootstrap_complete=True, bootstrap_admin_id=user_id
            ),
        ]
    )
    auth_db.commit()
    auth_db.execute(delete(User).where(User.id == user_id))
    auth_db.commit()
    for model in (LocalCredentials, ExternalIdentity, AuthSession, AuthActionToken):
        assert auth_db.scalar(select(model)) is None
    installation = auth_db.get(InstallationAuth, 1)
    assert installation.bootstrap_complete
    assert installation.bootstrap_admin_id == user_id


def test_orphan_credentials_rejected(auth_db):
    auth_db.add(
        LocalCredentials(
            user_id=uuid4(), normalized_email='orphan@example.com', password_hash='hash'
        )
    )
    with pytest.raises(IntegrityError):
        auth_db.commit()


def test_auth_timestamps_roundtrip_as_aware_utc(auth_db):
    user_id = add_user(auth_db)
    created = datetime(2026, 9, 10, 12, tzinfo=timezone(timedelta(hours=-4)))
    auth_db.add(
        AuthSession(
            token_digest='a' * 64,
            user_id=user_id,
            created_at=created,
            expires_at=created + timedelta(hours=24),
        )
    )
    auth_db.commit()
    session = auth_db.get(AuthSession, 'a' * 64)
    assert session.created_at == datetime(2026, 9, 10, 16, tzinfo=UTC)
    assert session.created_at.tzinfo is UTC
    assert session.expires_at.tzinfo is UTC


def test_naive_auth_timestamps_rejected(auth_db):
    user_id = add_user(auth_db)
    auth_db.add(
        AuthSession(
            token_digest='a' * 64,
            user_id=user_id,
            created_at=datetime(2026, 9, 10),
            expires_at=datetime.now(UTC),
        )
    )
    with pytest.raises(StatementError, match='must include a timezone'):
        auth_db.commit()


@pytest.mark.parametrize(
    'values',
    [
        {'id': 2, 'mode': 'local'},
        {'mode': 'unknown'},
        {'mode': 'local', 'bootstrap_complete': True},
    ],
)
def test_installation_state_constraints(auth_db, values):
    auth_db.add(InstallationAuth(**values))
    with pytest.raises(IntegrityError):
        auth_db.commit()


@pytest.mark.parametrize(
    'values',
    [
        {'token_digest': 'raw-token'},
        {'purpose': 'other'},
        {'expires_at': datetime(2020, 1, 1, tzinfo=UTC)},
    ],
)
def test_action_token_constraints(auth_db, values):
    user_id = add_user(auth_db)
    args = dict(
        token_digest='a' * 64,
        user_id=user_id,
        purpose='password_reset',
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    args.update(values)
    auth_db.add(AuthActionToken(**args))
    with pytest.raises(IntegrityError):
        auth_db.commit()


def test_authentication_migration_compiles_for_postgresql():
    migration = (
        Path(__file__).resolve().parents[3]
        / 'migrations/versions/158_add_authentication_foundation.py'
    )
    spec = importlib.util.spec_from_file_location('authentication_migration', migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output}
    )
    with Operations.context(context):
        module.upgrade()
        module.downgrade()
    sql = output.getvalue()
    assert 'TIMESTAMP WITH TIME ZONE' in sql
    assert 'FOREIGN KEY(user_id) REFERENCES "user" (id) ON DELETE CASCADE' in sql
    assert 'UNIQUE (normalized_email)' in sql
    assert 'bootstrap_admin_id UUID' in sql
    assert 'DROP TABLE installation_auth' in sql
