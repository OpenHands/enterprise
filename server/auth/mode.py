"""Select installation authentication once, before accepting auth traffic."""

import os
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from enum import StrEnum

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from storage.installation_auth import InstallationAuth


class AuthMode(StrEnum):
    LOCAL = 'local'
    KEYCLOAK = 'keycloak'


class AuthenticationConfigurationError(RuntimeError):
    """The installation cannot safely start with the requested auth state."""


BootstrapCallback = Callable[[AsyncSession, InstallationAuth], Awaitable[None]]
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

# A transaction-scoped lock serializes both first mode selection and bootstrap.
# Separate from the advisory lock used by Alembic.
INSTALLATION_AUTH_LOCK = 5498709385292711
LEGACY_TABLES = ('user', 'user_settings', 'auth_tokens', 'offline_tokens')
LOCAL_TABLES = (
    'local_credentials',
    'external_identities',
    'auth_sessions',
    'auth_action_tokens',
)
KEYCLOAK_CONFIG_KEYS = (
    'KEYCLOAK_SERVER_URL',
    'KEYCLOAK_SERVER_URL_EXT',
    'KEYCLOAK_REALM_NAME',
    'KEYCLOAK_CLIENT_ID',
    'KEYCLOAK_CLIENT_SECRET',
)
_auth_mode: AuthMode | None = None


def parse_keycloak_enabled(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in ('true', '1'):
        return True
    if normalized in ('false', '0'):
        return False
    raise AuthenticationConfigurationError(
        'KEYCLOAK_ENABLED must be true, 1, false, or 0; unset it to retain '
        'the recorded installation mode.'
    )


def get_auth_mode() -> AuthMode:
    """Read only the mode published after successful startup and bootstrap."""
    if _auth_mode is None:
        raise AuthenticationConfigurationError(
            'Authentication is not initialized. Run initialize_authentication '
            'after database migrations and before accepting authentication traffic.'
        )
    return _auth_mode


def is_keycloak_enabled() -> bool:
    return get_auth_mode() is AuthMode.KEYCLOAK


def _validate_keycloak_configuration(environ: Mapping[str, str]) -> None:
    required = ('KEYCLOAK_SERVER_URL', 'KEYCLOAK_REALM_NAME', 'KEYCLOAK_CLIENT_ID')
    missing = [key for key in required if not environ.get(key, '').strip()]
    if missing:
        raise AuthenticationConfigurationError(
            'This installation uses Keycloak. Restore the required settings: '
            + ', '.join(missing)
            + '. Removing configuration does not migrate an installation to local '
            'authentication.'
        )


async def _legacy_mode(session: AsyncSession, environ: Mapping[str, str]) -> AuthMode:
    connection = await session.connection()
    tables = await connection.run_sync(
        lambda conn: set(inspect(conn).get_table_names())
    )
    required = set(LEGACY_TABLES + LOCAL_TABLES)
    if missing := required - tables:
        raise AuthenticationConfigurationError(
            'Authentication schema is incomplete; run database migrations before '
            'startup. Missing tables: ' + ', '.join(sorted(missing))
        )

    populated = set()
    for table in LEGACY_TABLES + LOCAL_TABLES:
        # Table names are fixed application constants, never request input.
        exists = await session.scalar(text(f'SELECT 1 FROM "{table}" LIMIT 1'))
        if exists is not None:
            populated.add(table)

    if populated.intersection(LOCAL_TABLES):
        raise AuthenticationConfigurationError(
            'Authentication records exist without installation_auth state. Restore '
            'the installation record from backup before starting; automatic mode '
            'selection could attach existing accounts to the wrong login method.'
        )

    # Read only explicitly supplied values. In particular AUTH_WEB_HOST and the
    # computed KEYCLOAK_SERVER_URL_EXT default are not evidence of Keycloak.
    configured = any(environ.get(key, '').strip() for key in KEYCLOAK_CONFIG_KEYS)
    if populated or configured:
        return AuthMode.KEYCLOAK
    return AuthMode.LOCAL


def _bootstrap_completed(installation: InstallationAuth) -> bool:
    # The callback mutates these fields inside the transaction.
    return (
        installation.bootstrap_complete and installation.bootstrap_admin_id is not None
    )


async def initialize_auth_mode(
    session: AsyncSession,
    *,
    environ: Mapping[str, str] | None = None,
    bootstrap: BootstrapCallback | None = None,
) -> AuthMode:
    """Initialize inside the caller's transaction, without publishing readiness.

    ``bootstrap`` must create the account graph and mark the supplied installation
    complete in this same session. It must not commit or call remote services.
    PostgreSQL serializes concurrent replicas; SQLite is supported for unit tests.
    """
    settings = dict(os.environ if environ is None else environ)
    enabled = parse_keycloak_enabled(settings.get('KEYCLOAK_ENABLED'))
    requested = (
        None if enabled is None else (AuthMode.KEYCLOAK if enabled else AuthMode.LOCAL)
    )

    if session.get_bind().dialect.name == 'postgresql':
        await session.execute(
            text('SELECT pg_advisory_xact_lock(:lock_id)'),
            {'lock_id': INSTALLATION_AUTH_LOCK},
        )

    connection = await session.connection()
    has_installation_table = await connection.run_sync(
        lambda conn: inspect(conn).has_table('installation_auth')
    )
    if not has_installation_table:
        raise AuthenticationConfigurationError(
            'Authentication schema is incomplete; run database migrations before '
            'startup. Missing table: installation_auth'
        )

    installation = await session.scalar(
        select(InstallationAuth).where(InstallationAuth.id == 1).with_for_update()
    )
    if installation is None:
        detected = await _legacy_mode(session, settings)
        if requested is AuthMode.LOCAL and detected is AuthMode.KEYCLOAK:
            raise AuthenticationConfigurationError(
                'Existing identity data or explicit Keycloak configuration requires '
                'Keycloak. KEYCLOAK_ENABLED=false cannot migrate this installation. '
                'Restore its previous setting and prepare an identity migration.'
            )
        mode = requested or detected
        installation = InstallationAuth(id=1, mode=mode.value, bootstrap_complete=False)
        session.add(installation)
        await session.flush()
    else:
        try:
            mode = AuthMode(installation.mode)
        except ValueError as exc:
            raise AuthenticationConfigurationError(
                'The recorded installation authentication mode is invalid; '
                'restore the installation_auth record before startup.'
            ) from exc
        if requested is not None and requested is not mode:
            raise AuthenticationConfigurationError(
                f'This installation is recorded as {mode.value}. Changing '
                'KEYCLOAK_ENABLED does not migrate accounts; restore the previous '
                'setting and prepare an identity migration.'
            )

    if mode is AuthMode.KEYCLOAK:
        _validate_keycloak_configuration(settings)
    elif not installation.bootstrap_complete:
        if bootstrap is None:
            raise AuthenticationConfigurationError(
                'Local authentication requires administrator bootstrap. Set '
                'OH_BOOTSTRAP_ADMIN_EMAIL and OH_BOOTSTRAP_ADMIN_PASSWORD and '
                'initialize the bootstrap service before accepting traffic.'
            )
        await bootstrap(session, installation)
        if not _bootstrap_completed(installation):
            raise AuthenticationConfigurationError(
                'Administrator bootstrap did not complete in the installation '
                'transaction; authentication cannot become ready.'
            )
        await session.flush()
    return mode


async def initialize_authentication(
    *,
    session_factory: SessionFactory | None = None,
    environ: Mapping[str, str] | None = None,
    bootstrap: BootstrapCallback | None = None,
) -> AuthMode:
    """Commit installation state and bootstrap before publishing the active mode."""
    if session_factory is None:
        from storage.database import a_session_maker

        session_factory = a_session_maker

    global _auth_mode
    async with session_factory() as session:
        async with session.begin():
            mode = await initialize_auth_mode(
                session, environ=environ, bootstrap=bootstrap
            )
    _auth_mode = mode
    return mode


async def ensure_authentication_initialized() -> AuthMode:
    """Explicit initialization entry point for Enterprise workers and jobs."""
    if _auth_mode is not None:
        return _auth_mode
    from server.auth.local.bootstrap import bootstrap_local_admin

    return await initialize_authentication(bootstrap=bootstrap_local_admin)
