"""Initialize the immutable installation mode after migrations.

Run ``python -m server.auth.bootstrap`` from init containers. Offline recovery is
``python -m server.auth.bootstrap recover ACCOUNT_UUID`` and uses secure input.
"""

import argparse
import asyncio
import getpass
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth import auth_config
from server.auth.native_password import NativeAuthError, hash_password, normalize_email
from server.auth.native_types import SessionFactory
from server.services.native_account_service import create_profile, lock_native_lifecycle
from storage.database import a_session_maker
from storage.native_auth import AuthAccount, AuthInstallation, PasswordCredential


async def _has_legacy_identities(session: AsyncSession) -> bool:
    # Check identity-bearing tables even when the disposable User was deleted.
    # Names come exclusively from the database catalog, quoted by the dialect.
    names = (
        (
            await session.execute(
                text("""
        SELECT DISTINCT table_name FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND (column_name IN ('keycloak_user_id', 'user_id')
               OR table_name IN ('user', 'user_settings', 'stored_offline_tokens'))
          AND table_name NOT LIKE 'auth_%'
    """)
            )
        )
        .scalars()
        .all()
    )
    preparer = session.get_bind().dialect.identifier_preparer
    for name in names:
        if await session.scalar(
            text(f'SELECT EXISTS (SELECT 1 FROM {preparer.quote(name)} LIMIT 1)')
        ):
            return True
    return False


async def initialize_auth_installation(
    *, session_factory: SessionFactory | None = None
) -> None:
    if not auth_config.ENABLE_KEYCLOAK:
        from server.auth.saml_config import get_saml_settings

        get_saml_settings()
    factory = session_factory or a_session_maker
    async with factory() as session, session.begin():
        await lock_native_lifecycle(session)
        installation = await session.get(AuthInstallation, 1)
        if installation is not None:
            if installation.mode != auth_config.AUTH_MODE:
                raise RuntimeError(
                    'Configured authentication mode differs from this installation; changing modes is unsupported'
                )
            if installation.completed_at is not None:
                return
            raise RuntimeError(
                'Authentication installation is incomplete; operator investigation required'
            )
        if auth_config.ENABLE_KEYCLOAK:
            session.add(
                AuthInstallation(id=1, mode='keycloak', completed_at=datetime.now(UTC))
            )
            return
        auth_config.get_native_auth_settings()
        if await _has_legacy_identities(session) or await session.scalar(
            select(AuthAccount.id).limit(1)
        ):
            raise RuntimeError(
                'Native authentication requires an identity-empty database; existing installations must use Keycloak'
            )
        email_raw = os.getenv('SUPERADMIN_EMAIL')
        password = os.getenv('SUPERADMIN_PASSWORD')
        if not email_raw or not password:
            raise RuntimeError(
                'A fresh native installation requires SUPERADMIN_EMAIL and SUPERADMIN_PASSWORD'
            )
        email = normalize_email(email_raw)
        password_hash = await hash_password(password)
        account = AuthAccount(
            id=uuid4(), normalized_email=email, display_email=email_raw.strip()
        )
        session.add(account)
        await session.flush()
        session.add(
            PasswordCredential(
                account_id=account.id,
                normalized_login_email=email,
                display_email=email_raw.strip(),
                password_hash=password_hash,
            )
        )
        await create_profile(session, account, email_raw.strip(), superadmin=True)
        session.add(
            AuthInstallation(
                id=1,
                mode='native',
                bootstrap_account_id=account.id,
                completed_at=datetime.now(UTC),
            )
        )


async def verify_auth_installation(
    *, session_factory: SessionFactory | None = None
) -> None:
    factory = session_factory or a_session_maker
    if not auth_config.ENABLE_KEYCLOAK:
        auth_config.get_native_auth_settings()
        from server.auth.saml_config import get_saml_settings

        get_saml_settings()
    async with factory() as session:
        installation = await session.get(AuthInstallation, 1)
        if installation is None or installation.completed_at is None:
            raise RuntimeError(
                'Authentication installation has not been initialized; run python -m server.auth.bootstrap after migrations'
            )
        if installation.mode != auth_config.AUTH_MODE:
            raise RuntimeError(
                'Configured authentication mode differs from this installation; changing modes is unsupported'
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'command', nargs='?', choices=('initialize', 'recover'), default='initialize'
    )
    parser.add_argument('account_id', nargs='?', type=UUID)
    args = parser.parse_args()
    if args.command == 'initialize':
        asyncio.run(initialize_auth_installation())
        return
    if args.account_id is None:
        parser.error('recover requires the exact account UUID')
    if auth_config.ENABLE_KEYCLOAK:
        parser.error('native password recovery is unavailable in Keycloak mode')
    password = getpass.getpass('New password: ')
    confirm = getpass.getpass('Confirm new password: ')
    if password != confirm:
        parser.error('passwords do not match')

    async def recover() -> None:
        from server.services.native_auth_service import get_native_auth_service

        await verify_auth_installation()
        await get_native_auth_service().recover_password(args.account_id, password)

    try:
        asyncio.run(recover())
    except NativeAuthError as exc:
        parser.error(str(exc))
    # Audit only the exact target; never the password or token.
    print(
        f'Native password recovered and sessions revoked for account {args.account_id}'
    )


if __name__ == '__main__':
    main()
