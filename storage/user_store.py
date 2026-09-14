"""Store class for managing users."""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from openhands.app_server.utils.jsonpatch_compat import deep_merge
from openhands.sdk.settings import AGENT_SETTINGS_SCHEMA_VERSION
from server.constants import (
    DEFAULT_V1_ENABLED,
    LITE_LLM_API_URL,
    ORG_SETTINGS_VERSION,
    PERSONAL_WORKSPACE_VERSION_TO_MODEL,
    get_default_llm_base_url,
    get_default_llm_model,
)
from server.logger import logger
from storage.database import a_session_maker
from storage.encrypt_utils import (
    decrypt_legacy_value,
    encrypt_legacy_value,
)
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.role_store import RoleStore
from storage.user import User
from storage.user_settings import UserSettings
from utils.identity import UserIdentityClaims, resolve_display_name

# The max possible time to wait for another process to finish creating a user before retrying
_REDIS_CREATE_TIMEOUT_SECONDS = 30
# The delay to wait for another process to finish creating a user before trying to load again
_RETRY_LOAD_DELAY_SECONDS = 2
# Redis key prefix for user creation locks
_REDIS_USER_CREATION_KEY_PREFIX = 'create_user:'

# Role name whose row, when referenced by ``user.role_id``, makes a user a
# ``superadmin`` (see ``server.auth.authorization`` for the super-role model).
_SUPER_ADMIN_ROLE_NAME = 'admin'


class SuperAdminRevokeResult(str, Enum):
    """Outcome of an attempt to revoke a user's super-admin role.

    Returned (rather than raising) so the calling route can map each case
    to an appropriate HTTP status without sniffing exception types.
    """

    REVOKED = 'revoked'
    NOT_FOUND = 'not_found'
    NOT_SUPER_ADMIN = 'not_super_admin'
    LAST_SUPER_ADMIN = 'last_super_admin'


class UserStore:
    """Store for managing users."""

    @staticmethod
    async def record_login(user_id: str) -> None:
        """Record a successful login for a user."""
        async with a_session_maker() as session:
            user = await session.get(User, uuid.UUID(user_id))
            if not user:
                return
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if user.first_login_at is None:
                user.first_login_at = now
            user.last_login_at = now
            await session.commit()

    @staticmethod
    def _get_redis_client():
        """Get the shared async Redis client from enterprise storage."""
        from storage.redis import get_redis_client_async

        return get_redis_client_async()

    @staticmethod
    async def _acquire_user_creation_lock(user_id: str) -> bool:
        """Attempt to acquire a distributed lock for user creation.

        Returns True if the lock was acquired or if Redis is unavailable (fallback to no locking).
        Returns False if another process holds the lock.
        """
        from storage.redis import redis_exceptions

        redis_client = UserStore._get_redis_client()
        try:
            user_key = f'{_REDIS_USER_CREATION_KEY_PREFIX}{user_id}'
            lock_acquired = await redis_client.set(
                user_key, 1, nx=True, ex=_REDIS_CREATE_TIMEOUT_SECONDS
            )
            return bool(lock_acquired)
        except redis_exceptions.RedisError:
            logger.warning(
                'user_store:_acquire_user_creation_lock:redis_error',
                extra={'user_id': user_id},
            )
            return True  # Proceed without locking on error

    @staticmethod
    async def _release_user_creation_lock(user_id: str) -> bool:
        """Release the distributed lock for user creation.

        Returns True if the lock was released or if Redis is unavailable.
        Returns False if the lock could not be released.
        """
        from storage.redis import redis_exceptions

        redis_client = UserStore._get_redis_client()
        try:
            user_key = f'{_REDIS_USER_CREATION_KEY_PREFIX}{user_id}'
            deleted = await redis_client.delete(user_key)
            return bool(deleted)
        except redis_exceptions.RedisError:
            logger.warning(
                'user_store:_release_user_creation_lock:redis_error',
                extra={'user_id': user_id},
            )
            return True  # Proceed without locking on error

    @staticmethod
    async def get_user_by_id(user_id: str) -> User | None:
        """Load a persisted profile without authenticating or migrating it."""
        async with a_session_maker() as session:
            user = await session.scalar(
                select(User)
                .options(selectinload(User.org_members))
                .where(User.id == UUID(user_id))
            )
            if user is not None:
                user.sync_analytics_consent_with_tos()
            return user

    @staticmethod
    async def get_user_by_email(email: str) -> User | None:
        """Load a persisted profile by its normalized contact email."""
        if not email:
            return None
        async with a_session_maker() as session:
            return await session.scalar(
                select(User)
                .options(selectinload(User.org_members))
                .where(User.email == email.lower().strip())
            )

    @staticmethod
    async def list_users() -> list[User]:
        """List all users."""
        async with a_session_maker() as session:
            result = await session.execute(select(User))
            return list(result.scalars().all())

    @staticmethod
    async def update_current_org(user_id: str, org_id: UUID) -> Optional[User]:
        """Update the user's current organization.

        Args:
            user_id: The user's ID (Keycloak user ID)
            org_id: The organization ID to set as current

        Returns:
            User: The updated user object, or None if user not found
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(User).filter(User.id == uuid.UUID(user_id)).with_for_update()
            )
            user = result.scalars().first()
            if not user:
                return None

            user.current_org_id = org_id
            await session.commit()
            await session.refresh(user)
            return user

    @staticmethod
    async def mark_onboarding_completed(user_id: str) -> Optional[User]:
        """Mark the user's onboarding as completed.

        Args:
            user_id: The user's ID (Keycloak user ID)

        Returns:
            User: The updated user object, or None if user not found
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(User).filter(User.id == uuid.UUID(user_id)).with_for_update()
            )
            user = result.scalars().first()
            if not user:
                logger.warning(
                    'mark_onboarding_completed:user_not_found',
                    extra={'user_id': user_id},
                )
                return None

            user.onboarding_completed = True
            await session.commit()
            await session.refresh(user)
            logger.info(
                'mark_onboarding_completed:success',
                extra={'user_id': user_id},
            )
            return user

    @staticmethod
    async def _get_super_admin_role_id(session) -> int:
        """Resolve the role id that designates a super admin.

        Fails fast if the ``admin`` role row is missing -- it is seeded by
        migration, so its absence is a deployment error, not a runtime
        condition to be tolerated (mirrors the ``owner`` lookup in
        ``create_user``).
        """
        role = await RoleStore.get_role_by_name(_SUPER_ADMIN_ROLE_NAME, session)
        if role is None:
            raise ValueError(
                f'Super-admin role {_SUPER_ADMIN_ROLE_NAME!r} not found in database'
            )
        return role.id

    @staticmethod
    async def list_super_admins() -> list[User]:
        """List all users that currently hold the instance-level super-admin role.

        A super admin is a user whose ``user.role_id`` references the
        ``admin`` role row (distinct from any org-scoped ``org_member.role_id``).
        """
        async with a_session_maker() as session:
            admin_role_id = await UserStore._get_super_admin_role_id(session)
            result = await session.execute(
                select(User).filter(User.role_id == admin_role_id)
            )
            return list(result.scalars().all())

    @staticmethod
    async def grant_super_admin(user_id: str) -> Optional[User]:
        """Grant the instance-level super-admin role to an existing user.

        Sets ``user.role_id`` to the ``admin`` role row. Idempotent: if the
        user already holds the super-admin role the row is returned unchanged.

        Args:
            user_id: The target user's ID.

        Returns:
            The updated (or already-super-admin) user, or ``None`` if no user
            exists with the given id.
        """
        async with a_session_maker() as session:
            admin_role_id = await UserStore._get_super_admin_role_id(session)
            result = await session.execute(
                select(User).filter(User.id == uuid.UUID(user_id)).with_for_update()
            )
            user = result.scalars().first()
            if not user:
                return None

            if user.role_id != admin_role_id:
                user.role_id = admin_role_id
                await session.commit()
                await session.refresh(user)
                logger.info(
                    'user_store:grant_super_admin:granted',
                    extra={'user_id': user_id},
                )
            return user

    @staticmethod
    async def revoke_super_admin(user_id: str) -> SuperAdminRevokeResult:
        """Revoke the instance-level super-admin role from a user.

        Clears ``user.role_id``. Refuses to remove the **last** remaining
        super admin so an installation can never be left with no instance
        administrator (this also covers self-removal: a super admin may
        demote themselves as long as another super admin still exists).

        Concurrency: the whole set of current super admins is selected
        ``FOR UPDATE`` before the count/clear, so simultaneous revokes
        serialize. A transaction that waited re-evaluates the predicate
        after acquiring the lock, so it sees an up-to-date set and cannot
        race two "second-to-last" revocations down to zero. (On SQLite,
        used in tests, ``FOR UPDATE`` is a no-op but the surrounding
        transaction still serializes writes.)

        Args:
            user_id: The target user's ID.

        Returns:
            A :class:`SuperAdminRevokeResult` describing the outcome.
        """
        target_uuid = uuid.UUID(user_id)
        async with a_session_maker() as session:
            admin_role_id = await UserStore._get_super_admin_role_id(session)
            result = await session.execute(
                select(User).filter(User.role_id == admin_role_id).with_for_update()
            )
            super_admins = list(result.scalars().all())

            target = next((u for u in super_admins if u.id == target_uuid), None)
            if target is None:
                exists = await session.scalar(
                    select(User.id).filter(User.id == target_uuid)
                )
                return (
                    SuperAdminRevokeResult.NOT_FOUND
                    if exists is None
                    else SuperAdminRevokeResult.NOT_SUPER_ADMIN
                )

            if len(super_admins) <= 1:
                logger.warning(
                    'user_store:revoke_super_admin:refused_last_super_admin',
                    extra={'user_id': user_id},
                )
                return SuperAdminRevokeResult.LAST_SUPER_ADMIN

            target.role_id = None
            await session.commit()
            logger.info(
                'user_store:revoke_super_admin:revoked',
                extra={'user_id': user_id},
            )
            return SuperAdminRevokeResult.REVOKED

    @staticmethod
    async def get_first_owner_in_org(org_id: UUID) -> Optional[User]:
        """Get the first owner in an organization who accepted the Terms of Service.

        This user is considered the super admin for that org in self-hosted deployments.
        The super admin is identified as the owner with the earliest accepted_tos timestamp.

        Args:
            org_id: The organization UUID

        Returns:
            User: The first owner to accept TOS in this org, or None if not found.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(User)
                .join(OrgMember, OrgMember.user_id == User.id)
                .join(Role, Role.id == OrgMember.role_id)
                .filter(
                    OrgMember.org_id == org_id,
                    Role.name == 'owner',
                    User.accepted_tos.isnot(None),
                )
                .order_by(User.accepted_tos.asc())
                .limit(1)
            )
            return result.scalars().first()

    @staticmethod
    async def backfill_contact_name(
        user_id: str, user_info: UserIdentityClaims
    ) -> None:
        """Update contact_name on the personal org if it still has a username-style value.

        Called during login to gradually fix existing users whose contact_name
        was stored as their username (before the resolve_display_name fix).
        Preserves custom values that were set via the PATCH endpoint.
        """
        real_name = resolve_display_name(user_info)
        if not real_name:
            logger.debug(
                'backfill_contact_name:no_real_name',
                extra={'user_id': user_id},
            )
            return

        preferred_username = user_info.get('preferred_username', '')
        username = user_info.get('username', '')

        async with a_session_maker() as session:
            org_result = await session.execute(
                select(Org).filter(Org.id == uuid.UUID(user_id))
            )
            org = org_result.scalars().first()
            if not org:
                logger.debug(
                    'backfill_contact_name:org_not_found',
                    extra={'user_id': user_id},
                )
                return

            if org.contact_name in (preferred_username, username):
                logger.info(
                    'backfill_contact_name:updated',
                    extra={
                        'user_id': user_id,
                        'old': org.contact_name,
                        'new': real_name,
                    },
                )
                org.contact_name = real_name
                await session.commit()

    @staticmethod
    async def update_user_email(
        user_id: str,
        email: str | None = None,
        email_verified: bool | None = None,
    ) -> None:
        """Unconditionally update User.email and/or email_verified.

        Unlike backfill_user_email(), this overwrites existing values.
        No-op when both arguments are None.
        Missing user is logged as a warning and ignored.
        """
        if email is None and email_verified is None:
            return

        async with a_session_maker() as session:
            result = await session.execute(
                select(User).filter(User.id == uuid.UUID(user_id))
            )
            user = result.scalars().first()
            if not user:
                logger.warning(
                    'update_user_email:user_not_found',
                    extra={'user_id': user_id},
                )
                return

            if email is not None:
                user.email = email
            if email_verified is not None:
                user.email_verified = email_verified

            logger.info(
                'update_user_email:updated',
                extra={
                    'user_id': user_id,
                    'email_set': email is not None,
                    'email_verified_set': email_verified is not None,
                },
            )
            await session.commit()

    @staticmethod
    async def backfill_user_email(user_id: str, user_info: UserIdentityClaims) -> None:
        """Set User.email and email_verified from IDP if they are still NULL.

        Called during login to gradually fix existing users whose email
        was never persisted on the User record. Preserves non-NULL values
        (e.g. if a user manually changed their email).
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(User).filter(User.id == uuid.UUID(user_id))
            )
            user = result.scalars().first()
            if not user:
                logger.debug(
                    'backfill_user_email:user_not_found',
                    extra={'user_id': user_id},
                )
                return

            updated = False
            if user.email is None:
                user.email = user_info.get('email')
                updated = True

            if user.email_verified is None:
                user.email_verified = user_info.get('email_verified', False)
                updated = True

            if updated:
                logger.info(
                    'backfill_user_email:updated',
                    extra={
                        'user_id': user_id,
                        'email_set': user.email is not None,
                        'email_verified_set': user.email_verified is not None,
                    },
                )
                await session.commit()

    # Prevent circular imports
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from openhands.app_server.settings.settings_models import Settings

    @staticmethod
    async def create_default_settings(
        org_id: str,
        user_id: str,
        create_user: bool = True,
        add_user_to_litellm_team: bool = True,
    ) -> Optional['Settings']:
        logger.info(
            'UserStore:create_default_settings:start',
            extra={'org_id': org_id, 'user_id': user_id},
        )
        # You must log in before you get default settings
        if not org_id:
            return None

        from openhands.app_server.settings.settings_models import Settings

        default_settings = Settings(
            language='en', enable_proactive_conversation_starters=True
        )

        default_settings.v1_enabled = DEFAULT_V1_ENABLED

        from storage.lite_llm_manager import LiteLlmManager

        settings = await LiteLlmManager.create_entries(
            org_id,
            user_id,
            default_settings,
            create_user,
            add_user_to_team=add_user_to_litellm_team,
        )
        if not settings:
            logger.info(
                'UserStore:create_default_settings:litellm_create_failed',
                extra={'org_id': org_id},
            )
            return None

        return settings

    @staticmethod
    def get_kwargs_from_settings(settings: 'Settings'):
        kwargs = {
            normalized: getattr(settings, normalized)
            for c in User.__table__.columns
            if (normalized := c.name.lstrip('_')) and hasattr(settings, normalized)
        }
        return kwargs

    @staticmethod
    def get_kwargs_from_user_settings(user_settings: UserSettings):
        kwargs = {
            normalized: getattr(user_settings, normalized)
            for c in User.__table__.columns
            if (normalized := c.name.lstrip('_')) and hasattr(user_settings, normalized)
        }
        return kwargs

    @staticmethod
    def _sync_user_settings_from_org_member(
        user_settings: UserSettings, org_member: OrgMember
    ) -> None:
        user_settings.mcp_config = org_member.effective_mcp_config
        if org_member.llm_api_key and org_member.llm_api_key.get_secret_value():
            user_settings.llm_api_key = encrypt_legacy_value(
                org_member.llm_api_key.get_secret_value()
            )
        if (
            org_member.llm_api_key_for_byor
            and org_member.llm_api_key_for_byor.get_secret_value()
        ):
            user_settings.llm_api_key_for_byor = encrypt_legacy_value(
                org_member.llm_api_key_for_byor.get_secret_value()
            )

    @staticmethod
    def _create_user_settings_from_entities(
        user_id: str, org_member: OrgMember, user: User, org: Org
    ) -> UserSettings:
        """Create UserSettings from OrgMember, User, and Org data.

        Uses OrgMember values first. If an OrgMember field is None and there's
        a corresponding "default_" field in Org, use the Org value.
        Also pulls relevant fields from User.

        Args:
            user_id: The Keycloak user ID
            org_member: The OrgMember entity
            user: The User entity
            org: The Org entity

        Returns:
            A new UserSettings object populated from the entities
        """
        from storage.org_store import OrgStore

        member_agent_settings_diff = dict(org_member.agent_settings_diff)
        member_agent_settings_diff.pop('mcp_config', None)
        member_mcp_config = org_member.effective_mcp_config
        org_agent_settings = OrgStore.get_agent_settings_from_org(org)
        org_agent_settings_dump = org_agent_settings.model_dump(mode='json')
        org_agent_settings_dump.pop('mcp_config', None)
        agent_settings = deep_merge(org_agent_settings_dump, member_agent_settings_diff)

        member_conversation_settings_diff = dict(org_member.conversation_settings_diff)
        org_conversation_settings = OrgStore.get_conversation_settings_from_org(org)
        conversation_settings = deep_merge(
            org_conversation_settings.model_dump(mode='json'),
            member_conversation_settings_diff,
        )

        return UserSettings(
            keycloak_user_id=user_id,
            llm_api_key=org_member.llm_api_key.get_secret_value()
            if org_member.llm_api_key
            else None,
            llm_api_key_for_byor=org_member.llm_api_key_for_byor.get_secret_value()
            if org_member.llm_api_key_for_byor
            else None,
            accepted_tos=user.accepted_tos,
            enable_sound_notifications=user.enable_sound_notifications,
            language=user.language,
            user_consents_to_analytics=user.user_consents_to_analytics,
            email=user.email,
            email_verified=user.email_verified,
            git_user_name=user.git_user_name,
            git_user_email=user.git_user_email,
            remote_runtime_resource_factor=org.remote_runtime_resource_factor,
            billing_margin=org.billing_margin,
            enable_proactive_conversation_starters=org.enable_proactive_conversation_starters,
            sandbox_base_container_image=org.sandbox_base_container_image,
            sandbox_runtime_container_image=org.sandbox_runtime_container_image,
            user_version=org.org_version,
            search_api_key=org.search_api_key.get_secret_value()
            if org.search_api_key
            else None,
            sandbox_api_key=org.sandbox_api_key.get_secret_value()
            if org.sandbox_api_key
            else None,
            max_budget_per_task=org.max_budget_per_task,
            v1_enabled=org.v1_enabled,
            sandbox_grouping_strategy=org.sandbox_grouping_strategy,
            agent_settings=agent_settings,
            mcp_config=member_mcp_config,
            conversation_settings=conversation_settings,
            already_migrated=False,
        )

    @staticmethod
    def _get_org_kwargs_for_migration(
        user_settings: UserSettings, *, custom_settings: bool
    ) -> dict:
        from storage.org_store import OrgStore

        org_kwargs = OrgStore.get_kwargs_from_user_settings(user_settings)
        org_kwargs.pop('id', None)
        org_kwargs['org_version'] = ORG_SETTINGS_VERSION

        if custom_settings:
            org_kwargs['agent_settings'] = {
                'schema_version': AGENT_SETTINGS_SCHEMA_VERSION,
                'llm': {
                    'model': get_default_llm_model(),
                    'base_url': get_default_llm_base_url(),
                },
            }

        return org_kwargs

    @staticmethod
    def _has_custom_settings(
        user_settings: UserSettings, old_user_version: int | None
    ) -> bool:
        """Check if user has custom LLM settings that should be preserved.
        Returns True if user customized either model or base_url.

        Args:
            settings: The user's current settings
            old_user_version: The user's old settings version, if any

        Returns:
            True if user has custom settings, False if using old defaults
        """
        persisted_agent_settings = user_settings.agent_settings or {}
        llm_settings = persisted_agent_settings.get('llm', {})
        if isinstance(llm_settings, dict):
            user_model = llm_settings.get('model')
            user_base_url = llm_settings.get('base_url')
        else:
            user_model = None
            user_base_url = None

        user_model = user_model.strip() or None if user_model else None
        user_base_url = user_base_url.strip() or None if user_base_url else None

        # Custom base_url = definitely custom settings (BYOK)
        if user_base_url and user_base_url != LITE_LLM_API_URL:
            return True

        # No model set = using defaults
        if not user_model:
            return False

        # Check if model matches old version's default
        if (
            old_user_version
            and old_user_version <= ORG_SETTINGS_VERSION
            and old_user_version in PERSONAL_WORKSPACE_VERSION_TO_MODEL
        ):
            old_default_base = PERSONAL_WORKSPACE_VERSION_TO_MODEL[old_user_version]
            user_model_base = user_model.split('/')[-1]
            if user_model_base == old_default_base:
                return False  # Matches old default

        return True  # Custom model


def _is_legacy_value_encrypted(value: str) -> bool:
    """Check if a legacy value is encrypted by trying to decrypt it"""
    try:
        decrypt_legacy_value(value)
        return True
    except Exception:
        return False
