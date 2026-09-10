from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.auth import mode as auth_mode
from server.auth.mode import (
    AuthenticationConfigurationError,
    AuthMode,
    initialize_authentication,
    parse_keycloak_enabled,
)
from storage.auth_action_tokens import AuthActionToken
from storage.auth_sessions import AuthSession
from storage.auth_tokens import AuthTokens
from storage.external_identities import ExternalIdentity
from storage.installation_auth import InstallationAuth
from storage.local_credentials import LocalCredentials
from storage.org import Org
from storage.stored_offline_token import StoredOfflineToken
from storage.user import User
from storage.user_settings import UserSettings
from tests.unit.auth_schema import create_auth_schema

KEYCLOAK_ENV = {
    'KEYCLOAK_SERVER_URL': 'https://identity.example.com',
    'KEYCLOAK_REALM_NAME': 'openhands',
    'KEYCLOAK_CLIENT_ID': 'openhands',
}


@pytest.fixture
async def auth_sessions(monkeypatch):
    monkeypatch.setattr(auth_mode, '_auth_mode', None)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(create_auth_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def complete_bootstrap(session, installation):
    installation.bootstrap_admin_id = uuid4()
    installation.bootstrap_complete = True


async def start(auth_sessions, environ=None, bootstrap=complete_bootstrap):
    return await initialize_authentication(
        session_factory=auth_sessions,
        environ={} if environ is None else environ,
        bootstrap=bootstrap,
    )


async def insert_user(session):
    user_id = uuid4()
    await session.execute(insert(Org).values(id=user_id, name=str(user_id)))
    await session.execute(insert(User).values(id=user_id, current_org_id=user_id))
    return user_id


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        (None, None),
        ('true', True),
        ('1', True),
        ('TRUE', True),
        ('TrUe', True),
        ('false', False),
        ('0', False),
        ('FALSE', False),
        ('FaLsE', False),
    ],
)
def test_flag_spellings(value, expected):
    assert parse_keycloak_enabled(value) is expected


@pytest.mark.parametrize('value', ['', 'yes', 'off', '2', 'null'])
def test_invalid_flag_is_actionable(value):
    with pytest.raises(AuthenticationConfigurationError, match='true, 1, false, or 0'):
        parse_keycloak_enabled(value)


@pytest.mark.parametrize('flag', [None, 'false', '0', 'FALSE'])
async def test_fresh_installation_bootstraps_local(auth_sessions, flag):
    environ = {} if flag is None else {'KEYCLOAK_ENABLED': flag}
    bootstrap = AsyncMock(side_effect=complete_bootstrap)
    assert await start(auth_sessions, environ, bootstrap) is AuthMode.LOCAL
    assert auth_mode.get_auth_mode() is AuthMode.LOCAL
    assert not auth_mode.is_keycloak_enabled()
    bootstrap.assert_awaited_once()
    async with auth_sessions() as session:
        installation = await session.get(InstallationAuth, 1)
        assert installation.bootstrap_complete
        assert installation.bootstrap_admin_id is not None


@pytest.mark.parametrize('flag', ['true', '1', 'TRUE', 'TrUe'])
async def test_fresh_explicit_keycloak_never_bootstraps(auth_sessions, flag):
    bootstrap = AsyncMock()
    assert (
        await start(
            auth_sessions, {**KEYCLOAK_ENV, 'KEYCLOAK_ENABLED': flag}, bootstrap
        )
        is AuthMode.KEYCLOAK
    )
    assert auth_mode.is_keycloak_enabled()
    bootstrap.assert_not_awaited()


async def test_configured_keycloak_with_zero_users_is_retained(auth_sessions):
    assert await start(auth_sessions, KEYCLOAK_ENV) is AuthMode.KEYCLOAK


async def test_computed_external_hostname_is_not_legacy_evidence(auth_sessions):
    assert (
        await start(auth_sessions, {'AUTH_WEB_HOST': 'auth.example.com'})
        is AuthMode.LOCAL
    )


@pytest.mark.parametrize('key', auth_mode.KEYCLOAK_CONFIG_KEYS)
async def test_partial_explicit_keycloak_config_fails_safely(auth_sessions, key):
    with pytest.raises(AuthenticationConfigurationError, match='required settings'):
        await start(auth_sessions, {key: 'configured'})


@pytest.mark.parametrize('table', auth_mode.LEGACY_TABLES)
async def test_each_legacy_table_preserves_keycloak(auth_sessions, table):
    async with auth_sessions() as session, session.begin():
        if table == 'user':
            await insert_user(session)
        elif table == 'user_settings':
            session.add(UserSettings(keycloak_user_id=str(uuid4())))
        elif table == 'auth_tokens':
            session.add(
                AuthTokens(
                    keycloak_user_id=str(uuid4()),
                    identity_provider='github',
                    access_token='legacy',
                    refresh_token='legacy',
                    access_token_expires_at=0,
                    refresh_token_expires_at=0,
                )
            )
        else:
            session.add(
                StoredOfflineToken(user_id=str(uuid4()), offline_token='legacy')
            )
    # Missing config must fail with a Keycloak error rather than bootstrap local.
    with pytest.raises(AuthenticationConfigurationError, match='uses Keycloak'):
        await start(auth_sessions)
    with pytest.raises(AuthenticationConfigurationError, match='cannot migrate'):
        await start(auth_sessions, {'KEYCLOAK_ENABLED': 'false'})
    assert await start(auth_sessions, KEYCLOAK_ENV) is AuthMode.KEYCLOAK


async def test_recorded_local_stable_after_users_and_env_disappear(auth_sessions):
    await start(auth_sessions)
    async with auth_sessions() as session:
        admin_id = (await session.get(InstallationAuth, 1)).bootstrap_admin_id
    bootstrap = AsyncMock(side_effect=AssertionError('must never bootstrap twice'))
    assert await start(auth_sessions, {}, bootstrap) is AuthMode.LOCAL
    # Changed bootstrap input does not reset credentials or grant privileges.
    await start(
        auth_sessions,
        {
            'OH_BOOTSTRAP_ADMIN_EMAIL': 'replacement@example.com',
            'OH_BOOTSTRAP_ADMIN_PASSWORD': 'replacement',
        },
        bootstrap,
    )
    async with auth_sessions() as session:
        assert (await session.get(InstallationAuth, 1)).bootstrap_admin_id == admin_id
    bootstrap.assert_not_awaited()


async def test_recorded_keycloak_does_not_fallback_when_config_disappears(
    auth_sessions,
):
    await start(auth_sessions, KEYCLOAK_ENV)
    with pytest.raises(AuthenticationConfigurationError, match='uses Keycloak'):
        await start(auth_sessions)
    async with auth_sessions() as session:
        assert (await session.get(InstallationAuth, 1)).mode == 'keycloak'


@pytest.mark.parametrize('initial', ['local', 'keycloak'])
async def test_recorded_mode_rejects_flag_switch_even_with_zero_users(
    auth_sessions, initial
):
    await start(auth_sessions, {} if initial == 'local' else KEYCLOAK_ENV)
    with pytest.raises(
        AuthenticationConfigurationError, match='does not migrate accounts'
    ):
        await start(
            auth_sessions,
            {
                **KEYCLOAK_ENV,
                'KEYCLOAK_ENABLED': 'true' if initial == 'local' else 'false',
            },
        )


@pytest.mark.parametrize('table', auth_mode.LOCAL_TABLES)
async def test_unrecorded_local_data_is_ambiguous(auth_sessions, table):
    async with auth_sessions() as session, session.begin():
        user_id = await insert_user(session)
        if table == 'local_credentials':
            session.add(
                LocalCredentials(
                    user_id=user_id,
                    normalized_email='test@example.com',
                    password_hash='hash',
                )
            )
        elif table == 'external_identities':
            session.add(
                ExternalIdentity(
                    user_id=user_id,
                    connection='old',
                    issuer='issuer',
                    subject='subject',
                )
            )
        else:
            model = AuthSession if table == 'auth_sessions' else AuthActionToken
            values = (
                {'purpose': 'password_reset'} if table == 'auth_action_tokens' else {}
            )
            session.add(
                model(
                    token_digest='a' * 64,
                    user_id=user_id,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    **values,
                )
            )
    with pytest.raises(
        AuthenticationConfigurationError, match='without installation_auth'
    ):
        await start(auth_sessions, KEYCLOAK_ENV)


@pytest.mark.parametrize('table', ['auth_action_tokens', 'installation_auth'])
async def test_missing_schema_reports_migrations(auth_sessions, table):
    async with auth_sessions() as session, session.begin():
        await session.execute(text(f'DROP TABLE {table}'))
    with pytest.raises(
        AuthenticationConfigurationError, match='run database migrations'
    ):
        await start(auth_sessions)


async def test_missing_bootstrap_does_not_publish_mode(auth_sessions):
    with pytest.raises(
        AuthenticationConfigurationError, match='OH_BOOTSTRAP_ADMIN_EMAIL'
    ):
        await start(auth_sessions, bootstrap=None)
    with pytest.raises(AuthenticationConfigurationError, match='not initialized'):
        auth_mode.get_auth_mode()
    async with auth_sessions() as session:
        assert await session.get(InstallationAuth, 1) is None


async def test_incomplete_bootstrap_rolls_back_account_graph(auth_sessions):
    async def incomplete_bootstrap(session, installation):
        await insert_user(session)

    with pytest.raises(AuthenticationConfigurationError, match='did not complete'):
        await start(auth_sessions, bootstrap=incomplete_bootstrap)
    async with auth_sessions() as session:
        assert await session.scalar(select(User.id)) is None
        assert await session.get(InstallationAuth, 1) is None


async def test_bootstrap_failure_rolls_back_mode(auth_sessions):
    bootstrap = AsyncMock(side_effect=ValueError('invalid bootstrap password'))
    with pytest.raises(ValueError, match='invalid bootstrap password'):
        await start(auth_sessions, bootstrap=bootstrap)
    with pytest.raises(AuthenticationConfigurationError, match='not initialized'):
        auth_mode.get_auth_mode()
    async with auth_sessions() as session:
        assert await session.get(InstallationAuth, 1) is None
