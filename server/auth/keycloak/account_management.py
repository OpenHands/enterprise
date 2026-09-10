"""Explicit compatibility for Keycloak account hydration and remote lifecycle."""

import asyncio
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select

from keycloak.exceptions import KeycloakError
from server.auth.contracts import AuthenticationUnavailable
from server.auth.mode import is_keycloak_enabled
from server.logger import logger
from storage.database import a_session_maker
from storage.user import User
from storage.user_settings import UserSettings
from storage.user_store import UserStore


class AccountConflict(ValueError):
    """Existing upstream and Enterprise identities cannot be reconciled safely."""


class KeycloakAccountManagement:
    def __init__(self, token_manager=None):
        if not is_keycloak_enabled():
            raise RuntimeError('Keycloak account management is disabled')
        from server.auth.keycloak.token_manager import TokenManager

        self.tokens = token_manager or TokenManager()

    async def hydrate(
        self, user_id: UUID, user_info: dict | None = None
    ) -> User | None:
        """Migrate only legacy settings belonging to an authenticated subject."""
        identifier = str(user_id)
        while not await UserStore._acquire_user_creation_lock(identifier):
            await asyncio.sleep(2)
        try:
            user = await UserStore.get_user_by_id(identifier)
            if user is None:
                async with a_session_maker() as session:
                    legacy = await session.scalar(
                        select(UserSettings).where(
                            UserSettings.keycloak_user_id == identifier,
                            UserSettings.already_migrated.is_(False),
                        )
                    )
                if legacy is None:
                    return None
                info = user_info or await self.tokens.get_user_info_from_user_id(
                    identifier
                )
                if not info:
                    raise AuthenticationUnavailable(
                        'Account information is temporarily unavailable.'
                    )
                user = await UserStore.migrate_user(identifier, legacy, info)
            if user is not None and (user.email is None or user.email_verified is None):
                info = user_info or await self.tokens.get_user_info_from_user_id(
                    identifier
                )
                if info:
                    await UserStore.backfill_user_email(
                        identifier,
                        {
                            'email': info.get('email'),
                            'email_verified': info.get(
                                'email_verified', info.get('emailVerified', False)
                            ),
                        },
                    )
                    user = await UserStore.get_user_by_id(identifier)
            return user
        finally:
            await UserStore._release_user_creation_lock(identifier)

    async def provision(self, email: str, password: SecretStr) -> tuple[User, bool]:
        """Keep stable subjects and credentials when provisioning is retried."""
        identifier = await self.tokens.get_user_id_from_user_email(email)
        existing = await UserStore.get_user_by_id(identifier) if identifier else None
        matching_email = await UserStore.get_user_by_email(email)
        if matching_email is not None and (
            identifier is None
            or (existing is None and str(matching_email.id) != identifier)
        ):
            raise AccountConflict(
                'User exists in OpenHands without a matching Keycloak account. Repair the identity before retrying.'
            )
        created = False
        if identifier is None:
            try:
                identifier = await self.tokens.create_keycloak_user(
                    email, password.get_secret_value(), email_verified=True
                )
                created = True
            except AuthenticationUnavailable:
                raise
            except Exception as exc:
                # TokenManager translates SDK exceptions. The reread reconciles
                # only an identity that now exists; all other errors propagate.
                identifier = await self.tokens.get_user_id_from_user_email(email)
                if identifier is None:
                    raise AccountConflict('Failed to create Keycloak account.') from exc
        if existing is None:
            existing = await self.hydrate(UUID(identifier))
        if existing is None:
            existing = await UserStore.create_user(
                identifier,
                {
                    'sub': identifier,
                    'email': email,
                    'email_verified': True,
                    'preferred_username': email,
                },
            )
        if existing is None:
            raise AuthenticationUnavailable(
                'Account provisioning is incomplete. Retry the request.'
            )
        # Remote failure retains the committed account for explicit retry and
        # never compensates by removing a previously established identity.
        if created:
            try:
                offline = await self.tokens.request_offline_token(
                    username=email, password=password.get_secret_value()
                )
                await self.tokens.store_offline_token(
                    user_id=identifier, offline_token=offline
                )
            except Exception:
                # Browser login can acquire this compatibility credential later.
                # API keys and background provider access do not depend on it, so
                # preserve the successful response containing the initial password.
                logger.warning(
                    'keycloak_account:offline_credential_deferred',
                    extra={'user_id': identifier},
                )
        return existing, created

    def _admin(self):
        from server.auth.keycloak.manager import get_keycloak_admin

        return get_keycloak_admin(self.tokens.external)

    async def set_enabled(self, user_id: str, enabled: bool) -> None:
        try:
            await self._admin().a_update_user(
                user_id=user_id, payload={'enabled': enabled}
            )
        except KeycloakError as exc:
            if not enabled and exc.response_code == 404:
                return
            raise AuthenticationUnavailable(
                'Identity cleanup is pending. Retry the operation.'
            ) from exc

    async def delete(self, user_id: str) -> None:
        try:
            await self._admin().a_delete_user(user_id)
        except KeycloakError as exc:
            if exc.response_code == 404:
                return
            raise AuthenticationUnavailable(
                'Identity cleanup is pending. Retry the operation.'
            ) from exc
