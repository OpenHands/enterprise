"""Opt-in real PostgreSQL 17 authentication and migration regressions.

Set OH_AUTH_TEST_POSTGRES_PORT to an isolated localhost PostgreSQL 17 server.
The suite creates UUID-named databases, runs historical Alembic migrations,
and removes only those databases. Ordinary unit runs skip these tests.
"""

import asyncio
import importlib
import os
import subprocess
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import SecretStr
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth import mode
from server.auth.contracts import InvalidCredentials, IssuedSession, SessionExpired
from server.auth.local.accounts import create_local_account
from server.auth.local.actions import InvalidActionToken, LocalAccountActions
from server.auth.local.bootstrap import bootstrap_local_admin
from server.auth.local.credentials import LocalPasswordCredentialService
from server.auth.local.sessions import (
    LocalBrowserSessionBackend,
    lock_account,
    token_digest,
)
from server.auth.mode import (
    AuthenticationConfigurationError,
    AuthMode,
    initialize_authentication,
)
from storage import encrypt_utils
from storage.api_key import ApiKey
from storage.auth_action_tokens import AuthActionToken
from storage.auth_tokens import AuthTokens
from storage.installation_auth import InstallationAuth
from storage.local_credentials import LocalCredentials
from storage.org import Org
from storage.org_invitation import OrgInvitation
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User

PG_PORT = os.environ.get('OH_AUTH_TEST_POSTGRES_PORT')
pytestmark = pytest.mark.skipif(
    not PG_PORT,
    reason='Set OH_AUTH_TEST_POSTGRES_PORT to an isolated PostgreSQL 17 port.',
)
PASSWORD = SecretStr('PostgreSQL initial password')
REPLACEMENT = SecretStr('PostgreSQL replacement password')


def migrate(database, revision, log_dir, *, downgrade=False):
    env = {
        **os.environ,
        'DB_HOST': '127.0.0.1',
        'DB_PORT': str(PG_PORT),
        'DB_USER': 'postgres',
        'DB_PASS': 'postgres',
        'DB_NAME': database,
        'DB_DRIVER': 'pg8000',
        'DB_SSL_MODE': 'disable',
    }
    for key in ('GCP_DB_INSTANCE', 'GCP_PROJECT', 'GCP_REGION'):
        env.pop(key, None)
    result = subprocess.run(
        ['uv', 'run', 'alembic', 'downgrade' if downgrade else 'upgrade', revision],
        env=env,
        capture_output=True,
        text=True,
    )
    (log_dir / f'{database}-{revision}.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, (result.stdout + result.stderr)[-2000:]


@pytest.fixture(scope='module')
def migration_logs(tmp_path_factory):
    return tmp_path_factory.mktemp('auth-postgres-migrations')


@pytest.fixture(scope='module')
def postgres_template(migration_logs):
    # This fixture never targets the application's configured database. It uses
    # an explicitly selected local test port and only creates/removes UUID-named
    # test databases. Run it against the disposable PostgreSQL 17 CI container.
    admin = create_engine(
        f'postgresql+psycopg2://postgres@127.0.0.1:{PG_PORT}/postgres',
        isolation_level='AUTOCOMMIT',
    )
    name = 'auth_test_template_' + uuid4().hex[:12]
    with admin.connect() as connection:
        assert connection.scalar(text('SHOW server_version_num')).startswith('17')
        connection.execute(text(f'CREATE DATABASE {name}'))
    try:
        migrate(name, '157', migration_logs)
        yield admin, name
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE {name} WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def database_name(postgres_template):
    admin, template = postgres_template
    name = 'auth_test_' + uuid4().hex[:12]
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE {name} TEMPLATE {template}'))
    yield name
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE {name} WITH (FORCE)'))


@pytest.fixture(autouse=True)
def isolated_crypto(monkeypatch):
    monkeypatch.setattr(
        encrypt_utils,
        '_jwt_service',
        JwtService([EncryptionKey(key=SecretStr(uuid4().hex + uuid4().hex))]),
    )
    monkeypatch.setattr(mode, '_auth_mode', None)


@pytest.fixture
async def database(database_name, migration_logs):
    migrate(database_name, 'head', migration_logs)
    engine = create_async_engine(
        f'postgresql+asyncpg://postgres@127.0.0.1:{PG_PORT}/{database_name}',
        pool_size=12,
    )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def make_user(factory, email='user@example.com', **kwargs):
    async with factory() as session, session.begin():
        return await create_local_account(session, email, PASSWORD, **kwargs)


async def counts(factory):
    async with factory() as session:
        return [
            await session.scalar(select(func.count()).select_from(model))
            for model in (User, Org, OrgMember, LocalCredentials, InstallationAuth)
        ]


async def test_actual_eight_replica_bootstrap_and_restart_history(
    database, monkeypatch
):
    monkeypatch.setenv('OH_BOOTSTRAP_ADMIN_EMAIL', ' Admin+PG@EXAMPLE.com ')
    monkeypatch.setenv('OH_BOOTSTRAP_ADMIN_PASSWORD', PASSWORD.get_secret_value())
    modes = await asyncio.gather(
        *[
            initialize_authentication(
                session_factory=database, environ={}, bootstrap=bootstrap_local_admin
            )
            for _ in range(8)
        ]
    )
    assert modes == [AuthMode.LOCAL] * 8
    assert await counts(database) == [1, 1, 1, 1, 1]
    async with database() as session, session.begin():
        installation = await session.get(InstallationAuth, 1)
        user_id = installation.bootstrap_admin_id
        user = await session.get(User, user_id)
        credential = await session.get(LocalCredentials, user_id)
        member = await session.get(OrgMember, (user_id, user_id))
        assert user.id == user.current_org_id == member.org_id
        assert (await session.get(Role, user.role_id)).name == 'admin'
        assert (await session.get(Role, member.role_id)).name == 'owner'
        assert user.email == 'admin+pg@example.com'
        assert credential.password_hash.startswith('$argon2id$')
        assert credential.must_change_password and not user.email_verified
        saved_hash = credential.password_hash
        user.role_id = None
    monkeypatch.setenv('OH_BOOTSTRAP_ADMIN_EMAIL', 'changed@example.com')
    monkeypatch.setenv('OH_BOOTSTRAP_ADMIN_PASSWORD', 'invalid')
    await initialize_authentication(
        session_factory=database, environ={}, bootstrap=bootstrap_local_admin
    )
    async with database() as session, session.begin():
        assert (await session.get(User, user_id)).role_id is None
        assert (
            await session.get(LocalCredentials, user_id)
        ).password_hash == saved_hash
        await session.execute(delete(OrgMember).where(OrgMember.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.execute(delete(Org).where(Org.id == user_id))
    await initialize_authentication(
        session_factory=database, environ={}, bootstrap=bootstrap_local_admin
    )
    assert await counts(database) == [0, 0, 0, 0, 1]
    async with database() as session:
        installation = await session.get(InstallationAuth, 1)
        assert (
            installation.bootstrap_admin_id == user_id
            and installation.bootstrap_complete
        )


async def test_actual_bootstrap_transaction_rollback(database, monkeypatch):
    monkeypatch.setenv('OH_BOOTSTRAP_ADMIN_EMAIL', 'admin@example.com')
    monkeypatch.setenv('OH_BOOTSTRAP_ADMIN_PASSWORD', PASSWORD.get_secret_value())

    async def fail_after_graph(session, installation):
        await bootstrap_local_admin(session, installation)
        raise RuntimeError('simulated process failure before commit')

    with pytest.raises(RuntimeError):
        await initialize_authentication(
            session_factory=database, environ={}, bootstrap=fail_after_graph
        )
    assert await counts(database) == [0, 0, 0, 0, 0]
    with pytest.raises(AuthenticationConfigurationError):
        mode.get_auth_mode()
    await initialize_authentication(
        session_factory=database, environ={}, bootstrap=bootstrap_local_admin
    )
    assert await counts(database) == [1, 1, 1, 1, 1]


async def test_actual_parallel_reset_consumes_once_and_revokes_sessions(database):
    user = await make_user(database, must_change_password=False)
    actions = LocalAccountActions(database)
    backend = LocalBrowserSessionBackend(database)
    old_sessions = await asyncio.gather(*[backend.issue(user.id) for _ in range(4)])
    email = await actions.request_reset(user.email)
    with pytest.raises(InvalidActionToken):
        await actions.verify_email(email.token)
    results = await asyncio.gather(
        *[
            actions.reset_password(
                email.token, SecretStr(f'concurrent replacement password {index}')
            )
            for index in range(8)
        ],
        return_exceptions=True,
    )
    assert results.count(user.id) == 1
    assert sum(isinstance(result, InvalidActionToken) for result in results) == 7
    winner = results.index(user.id)
    assert (
        await LocalPasswordCredentialService(database).verify(
            user.email, SecretStr(f'concurrent replacement password {winner}')
        )
    ).id == user.id
    for issued in old_sessions:
        with pytest.raises(SessionExpired):
            await backend.validate(issued.token)
    async with database() as session:
        assert (
            await session.get(AuthActionToken, token_digest(email.token))
        ).consumed_at


async def test_actual_parallel_verification_consumes_once_and_changes_both_emails(
    database,
):
    user = await make_user(database, must_change_password=False)
    actions = LocalAccountActions(database)
    reset = await actions.request_reset(user.email)
    email = await actions.request_verification(user.id, 'replacement@example.com')
    results = await asyncio.gather(
        *[actions.verify_email(email.token) for _ in range(8)], return_exceptions=True
    )
    assert results.count(user.id) == 1
    assert sum(isinstance(result, InvalidActionToken) for result in results) == 7
    with pytest.raises(InvalidActionToken):
        await actions.reset_password(reset.token, REPLACEMENT)
    async with database() as session:
        assert (await session.get(User, user.id)).email == 'replacement@example.com'
        assert (
            await session.get(LocalCredentials, user.id)
        ).normalized_email == 'replacement@example.com'
        assert (
            await session.get(Org, user.id)
        ).contact_email == 'replacement@example.com'


async def test_actual_password_change_serializes_and_replaces_session(database):
    user = await make_user(database)
    passwords = LocalPasswordCredentialService(database)
    backend = LocalBrowserSessionBackend(database)
    old = await passwords.login(user.email, PASSWORD)
    results = await asyncio.gather(
        *[
            passwords.change_and_issue(
                user.id,
                PASSWORD,
                SecretStr(f'new password for concurrent change {index}'),
            )
            for index in range(8)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, IssuedSession) for result in results) == 1
    assert sum(isinstance(result, InvalidCredentials) for result in results) == 7
    with pytest.raises(SessionExpired):
        await backend.validate(old.token)
    winner = next(result for result in results if isinstance(result, IssuedSession))
    assert not (await backend.validate(winner.token)).restricted


async def test_actual_reset_racing_old_password_logins_cannot_resurrect_access(
    database,
):
    user = await make_user(database, must_change_password=False)
    passwords = LocalPasswordCredentialService(database)
    actions = LocalAccountActions(database)
    backend = LocalBrowserSessionBackend(database)
    email = await actions.request_reset(user.email)
    # All use the real User -> LocalCredentials lock ordering. A login before
    # reset must be revoked; one after reset must reject the old password.
    results = await asyncio.gather(
        actions.reset_password(email.token, REPLACEMENT),
        *[passwords.login(user.email, PASSWORD) for _ in range(8)],
        return_exceptions=True,
    )
    assert results[0] == user.id
    for result in results[1:]:
        assert isinstance(result, (IssuedSession, InvalidCredentials))
        if isinstance(result, IssuedSession):
            with pytest.raises(SessionExpired):
                await backend.validate(result.token)
    assert (await passwords.verify(user.email, REPLACEMENT)).id == user.id


async def test_actual_reset_waits_for_credential_lock_and_rechecks_expiry(database):
    user = await make_user(database, must_change_password=False)
    actions = LocalAccountActions(database)
    email = await actions.request_reset(user.email)
    async with database() as holder, holder.begin():
        await lock_account(holder, user.id)
        async with database() as writer, writer.begin():
            record = await writer.get(AuthActionToken, token_digest(email.token))
            record.created_at = datetime.now(UTC) - timedelta(hours=1)
            record.expires_at = datetime.now(UTC) + timedelta(milliseconds=300)
        task = asyncio.create_task(actions.reset_password(email.token, REPLACEMENT))
        await asyncio.sleep(0.6)
        assert not task.done()
    with pytest.raises(InvalidActionToken):
        await task
    assert (
        await LocalPasswordCredentialService(database).verify(user.email, PASSWORD)
    ).id == user.id


async def test_actual_enrollment_consumes_once_with_explicit_role(database):
    owner = await make_user(database)
    async with database() as session, session.begin():
        role_id = await session.scalar(select(Role.id).where(Role.name == 'admin'))
        invitation = OrgInvitation(
            token=uuid4().hex,
            org_id=owner.id,
            email='invited@example.com',
            inviter_id=owner.id,
            role_id=role_id,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1),
        )
        session.add(invitation)
        await session.flush()
    actions = LocalAccountActions(database)
    results = await asyncio.gather(
        *[actions.enroll(SecretStr(invitation.token), PASSWORD) for _ in range(8)],
        return_exceptions=True,
    )
    assert sum(isinstance(result, IssuedSession) for result in results) == 1
    assert sum(isinstance(result, InvalidActionToken) for result in results) == 7
    winner = next(result for result in results if isinstance(result, IssuedSession))
    async with database() as session:
        user = await session.get(User, winner.principal.user_id)
        assert user.email_verified and user.email == 'invited@example.com'
        assert user.role_id is None
        assert (await session.get(OrgMember, (owner.id, user.id))).role_id == role_id
        assert (
            await session.get(OrgInvitation, invitation.id)
        ).accepted_by_user_id == user.id


async def test_legacy_rows_and_provider_constraints_survive_actual_migrations(
    database_name,
    migration_logs,
):
    engine = create_async_engine(
        f'postgresql+asyncpg://postgres@127.0.0.1:{PG_PORT}/{database_name}'
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = [uuid4(), uuid4()]
    async with factory() as session, session.begin():
        role_id = await session.scalar(select(Role.id).where(Role.name == 'owner'))
        for user_id in ids:
            session.add(Org(id=user_id, name=f'legacy_{user_id}'))
            await session.flush()
            session.add(
                User(
                    id=user_id,
                    current_org_id=user_id,
                    email='duplicate@example.com',
                    is_disabled=False,
                )
            )
            await session.flush()
            session.add(
                OrgMember(
                    org_id=user_id,
                    user_id=user_id,
                    role_id=role_id,
                    llm_api_key=SecretStr('legacy-member-key'),
                )
            )
            session.add(
                ApiKey(
                    key=f'legacy-api-{user_id}', user_id=str(user_id), org_id=user_id
                )
            )
            await session.execute(
                text(
                    'INSERT INTO auth_tokens (keycloak_user_id,identity_provider,access_token,refresh_token,access_token_expires_at,refresh_token_expires_at) VALUES (:user_id,:provider,:access,:refresh,:expires,:refresh_expires)'
                ),
                {
                    'user_id': str(user_id),
                    'provider': 'github',
                    'access': 'legacy-ciphertext',
                    'refresh': 'legacy-refresh-ciphertext',
                    'expires': 1234567890,
                    'refresh_expires': 2234567890,
                },
            )
    await engine.dispose()
    migrate(database_name, '158', migration_logs)
    migrate(database_name, '159', migration_logs)
    assert (
        await initialize_authentication(
            session_factory=factory,
            environ={
                'KEYCLOAK_SERVER_URL': 'https://unreachable-idp.invalid',
                'KEYCLOAK_REALM_NAME': 'openhands',
                'KEYCLOAK_CLIENT_ID': 'openhands',
            },
            bootstrap=bootstrap_local_admin,
        )
        is AuthMode.KEYCLOAK
    )
    with pytest.raises(AuthenticationConfigurationError):
        await initialize_authentication(
            session_factory=factory,
            environ={'KEYCLOAK_ENABLED': 'false'},
            bootstrap=bootstrap_local_admin,
        )
    async with factory() as session, session.begin():
        assert (
            await session.scalar(text('SELECT version_num FROM alembic_version'))
            == '159'
        )
        assert list(
            (await session.scalars(select(User.id).order_by(User.id))).all()
        ) == sorted(ids)
        assert await session.scalar(select(func.count()).select_from(OrgMember)) == 2
        assert await session.scalar(select(func.count()).select_from(ApiKey)) == 2
        rows = (await session.scalars(select(AuthTokens))).all()
        assert all(
            row.credential_kind == 'oauth'
            and row.provider_account_id is None
            and row.access_token == 'legacy-ciphertext'
            and row.refresh_token == 'legacy-refresh-ciphertext'
            for row in rows
        )
        session.add(
            LocalCredentials(
                user_id=ids[0],
                normalized_email='duplicate@example.com',
                password_hash='not-used-in-this-constraint-test',
                must_change_password=True,
            )
        )
    with pytest.raises(IntegrityError):
        async with factory() as session, session.begin():
            session.add(
                LocalCredentials(
                    user_id=ids[1],
                    normalized_email='duplicate@example.com',
                    password_hash='not-used-in-this-constraint-test',
                    must_change_password=True,
                )
            )
    async with factory() as session, session.begin():
        session.add(
            AuthTokens(
                keycloak_user_id=str(ids[0]),
                identity_provider='gitlab',
                access_token='manual-ciphertext',
                credential_kind='manual',
                provider_account_id='123',
                provider_host='gitlab.example.com',
            )
        )
    with pytest.raises(IntegrityError):
        async with factory() as session, session.begin():
            session.add(
                AuthTokens(
                    keycloak_user_id=str(ids[1]),
                    identity_provider='gitlab',
                    access_token='manual-ciphertext',
                    credential_kind='manual',
                    provider_account_id='123',
                    provider_host='gitlab.example.com',
                )
            )
    with pytest.raises(IntegrityError):
        async with factory() as session, session.begin():
            session.add(
                AuthTokens(
                    keycloak_user_id=str(ids[1]),
                    identity_provider='bitbucket',
                    access_token='manual-ciphertext',
                    credential_kind='manual',
                    provider_account_id='456',
                    provider_host='bitbucket.org',
                    refresh_token='forbidden-refresh',
                )
            )
    async with factory() as session, session.begin():
        manual = await session.scalar(
            select(AuthTokens).where(AuthTokens.credential_kind == 'manual')
        )
        assert (
            manual.refresh_token
            is manual.access_token_expires_at
            is manual.refresh_token_expires_at
            is None
        )
    await engine.dispose()
    # Runtime logging can suppress the CLI's exception text. Check its failure
    # and the migration function's explicit guard independently on real PG.
    with pytest.raises(AssertionError):
        migrate(database_name, '157', migration_logs, downgrade=True)
    sync_engine = create_engine(
        f'postgresql+psycopg2://postgres@127.0.0.1:{PG_PORT}/{database_name}'
    )
    migration = importlib.import_module('migrations.versions.159_provider_credentials')
    try:
        with sync_engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(
                    RuntimeError, match='Disconnect manual provider credentials'
                ):
                    migration.downgrade()
    finally:
        sync_engine.dispose()
    async with factory() as session, session.begin():
        assert (
            await session.scalar(text('SELECT version_num FROM alembic_version'))
            == '159'
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuthTokens)
                .where(AuthTokens.credential_kind == 'manual')
            )
            == 1
        )
        await session.execute(
            delete(AuthTokens).where(AuthTokens.credential_kind == 'manual')
        )
        await session.execute(delete(LocalCredentials))
    await engine.dispose()
    migrate(database_name, '157', migration_logs, downgrade=True)
    migrate(database_name, 'head', migration_logs)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 2
        assert await session.scalar(select(func.count()).select_from(OrgMember)) == 2
        assert await session.scalar(select(func.count()).select_from(ApiKey)) == 2
        assert await session.scalar(select(func.count()).select_from(AuthTokens)) == 2
    await engine.dispose()
