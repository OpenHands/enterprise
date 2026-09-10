"""One-time administrator bootstrap, inside the installation transaction."""

import os

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.local.accounts import create_local_account
from server.auth.mode import AuthenticationConfigurationError
from storage.installation_auth import InstallationAuth


async def bootstrap_local_admin(
    session: AsyncSession, installation: InstallationAuth
) -> None:
    if installation.bootstrap_complete:
        return
    email = os.environ.get('OH_BOOTSTRAP_ADMIN_EMAIL')
    password = os.environ.get('OH_BOOTSTRAP_ADMIN_PASSWORD')
    if not email or not password:
        raise AuthenticationConfigurationError(
            'Initial local startup requires OH_BOOTSTRAP_ADMIN_EMAIL and '
            'OH_BOOTSTRAP_ADMIN_PASSWORD (15 to 1024 characters).'
        )
    try:
        user = await create_local_account(
            session, email, SecretStr(password), instance_admin=True
        )
    except ValueError:
        raise AuthenticationConfigurationError(
            'Invalid bootstrap credentials. OH_BOOTSTRAP_ADMIN_EMAIL must be a '
            'valid email and OH_BOOTSTRAP_ADMIN_PASSWORD must contain 15 to 1024 characters.'
        ) from None
    installation.bootstrap_admin_id = user.id
    installation.bootstrap_complete = True
    await session.flush()
