"""Recover an existing local instance administrator: python -m server.auth.local.recover_admin."""

import asyncio
import getpass

from pydantic import SecretStr
from sqlalchemy import delete, select

from server.auth.local.passwords import hash_password
from server.auth.local.sessions import lock_account, revoke_all_in_transaction
from server.auth.mode import AuthenticationConfigurationError, AuthMode, SessionFactory
from storage.auth_action_tokens import AuthActionToken
from storage.database import a_session_maker
from storage.installation_auth import InstallationAuth
from storage.local_credentials import LocalCredentials, normalize_login_email
from storage.role import Role


async def recover_admin(
    email: str, password: SecretStr, *, session_factory: SessionFactory | None = None
) -> None:
    """Reset an active, existing instance admin; never bootstrap or alter roles."""
    factory = session_factory or a_session_maker
    hashed = await hash_password(password)
    async with factory() as session, session.begin():
        installation = await session.get(InstallationAuth, 1)
        if (
            installation is None
            or installation.mode != AuthMode.LOCAL
            or not installation.bootstrap_complete
        ):
            raise AuthenticationConfigurationError(
                'Recovery requires an initialized local installation.'
            )
        user_id = await session.scalar(
            select(LocalCredentials.user_id).where(
                LocalCredentials.normalized_email == normalize_login_email(email)
            )
        )
        if user_id is None:
            raise ValueError(
                'The account is not an active local instance administrator.'
            )
        user, credential = await lock_account(session, user_id)
        role = await session.get(Role, user.role_id) if user.role_id else None
        if role is None or role.name != 'admin':
            raise ValueError(
                'The account is not an active local instance administrator.'
            )
        credential.password_hash = hashed
        credential.must_change_password = False
        await revoke_all_in_transaction(session, user_id)
        await session.execute(
            delete(AuthActionToken).where(AuthActionToken.user_id == user_id)
        )


def main() -> None:
    email = input('Existing administrator email: ')
    password = SecretStr(getpass.getpass('New password (15 to 1024 characters): '))
    confirmation = SecretStr(getpass.getpass('Repeat new password: '))
    if password.get_secret_value() != confirmation.get_secret_value():
        raise SystemExit('Passwords do not match.')
    try:
        asyncio.run(recover_admin(email, password))
    except Exception:
        # Avoid a traceback containing entered credentials or database parameters.
        raise SystemExit(
            'Recovery failed. Verify the initialized local installation, active administrator email, and password length.'
        ) from None
    print('Administrator password changed. Existing browser sessions were revoked.')


if __name__ == '__main__':
    main()
