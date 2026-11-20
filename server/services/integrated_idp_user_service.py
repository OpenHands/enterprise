"""Admin-created users for the integrated password IDP."""

from __future__ import annotations

from dataclasses import dataclass

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.integrated_idp_identity import derive_idp_user_id
from server.auth.password_hashing import hash_password
from storage.auth_provider import AuthProvider
from storage.auth_provider_user_store import AuthProviderUserStore
from storage.user import User
from storage.user_store import UserStore


class IntegratedIdpUserDisabledError(Exception):
    """Raised when an admin tries to set a password on a disabled account."""


@dataclass(frozen=True)
class IntegratedIdpPasswordUserResult:
    user: User
    created: bool


async def create_or_update_integrated_idp_password_user(
    *, provider: AuthProvider, email: str, password: str
) -> IntegratedIdpPasswordUserResult:
    """Create or update a password-login user without granting super-admin.

    If the email already maps to an account, its integrated-IDP password is set
    or reset. Otherwise a normal human user is created with automatic
    first-user/super-admin designation suppressed.
    """
    normalized_email = email.strip().lower()
    user = await find_integrated_idp_user(provider, normalized_email)
    created = False

    if user is not None and (user.is_disabled or user.deletion_pending):
        raise IntegratedIdpUserDisabledError(normalized_email)

    if user is None:
        user_id = derive_idp_user_id(normalized_email)
        user_info = {
            'email': normalized_email,
            'email_verified': True,
            'preferred_username': normalized_email,
        }
        user = await UserStore.create_user(
            user_id, user_info, suppress_auto_superadmin=True
        )
        if user is None:
            raise RuntimeError(
                f'Failed to create integrated IDP user {normalized_email}'
            )
        created = True

    await UserStore.set_password_hash(str(user.id), hash_password(password))
    await AuthProviderUserStore().link(
        provider.id, user.id, normalized_email, normalized_email
    )
    logger.info(
        'integrated_idp_user:password_user_saved',
        extra={
            'user_id': str(user.id),
            'email': normalized_email,
            'created': created,
        },
    )
    return IntegratedIdpPasswordUserResult(user=user, created=created)


async def find_integrated_idp_user(provider: AuthProvider, email: str) -> User | None:
    normalized_email = email.strip().lower()
    link = await AuthProviderUserStore().get(provider.id, normalized_email)
    if link is not None:
        return await UserStore.get_user_by_id(str(link.user_id))
    user = await UserStore.get_user_by_id(derive_idp_user_id(normalized_email))
    if user is None:
        user = await UserStore.get_user_by_email(normalized_email)
    return user


__all__ = [
    'IntegratedIdpPasswordUserResult',
    'IntegratedIdpUserDisabledError',
    'create_or_update_integrated_idp_password_user',
    'find_integrated_idp_user',
]
