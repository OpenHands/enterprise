"""Store class for managing users."""

import uuid
from typing import Optional, Protocol

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from server.auth.native_password import NativeAuthError
from server.constants import (
    DEFAULT_V1_ENABLED,
)
from server.logger import logger
from storage.database import a_session_maker
from storage.encrypt_utils import (
    decrypt_legacy_model,
    decrypt_legacy_value,
    encrypt_legacy_value,
)
from storage.org import Org
from storage.org_default_settings import (
    apply_configured_org_condenser_default,
)
from storage.org_member import OrgMember
from storage.role_store import RoleStore
from storage.user import User
from storage.user_settings import UserSettings
from storage.user_store import UserStore, _is_legacy_value_encrypted
from utils.identity import UserIdentityClaims, resolve_display_name


class AccountProfileProvisioning(Protocol):
    def require_programmatic_provisioning(self) -> None: ...
    async def create_user(
        self, user_id: str, user_info: UserIdentityClaims, role_id: int | None = None
    ) -> User | None: ...
    async def migrate_user(
        self, user_id: str, user_settings: UserSettings, user_info: UserIdentityClaims
    ) -> User | None: ...
    async def downgrade_user(self, user_id: str) -> UserSettings | None: ...


class OpenHandsAccountProfileProvisioning:
    def require_programmatic_provisioning(self) -> None:
        raise NativeAuthError('Account setup links require global user management', 403)

    async def create_user(
        self, user_id: str, user_info: UserIdentityClaims, role_id: int | None = None
    ) -> User | None:
        raise NativeAuthError('Accounts require an account setup link', 403)

    async def migrate_user(
        self, user_id: str, user_settings: UserSettings, user_info: UserIdentityClaims
    ) -> User | None:
        raise NativeAuthError('Legacy identity migration is unavailable', 403)

    async def downgrade_user(self, user_id: str) -> UserSettings | None:
        raise NativeAuthError('Accounts without Keycloak cannot be downgraded', 403)


class KeycloakAccountProfileProvisioning:
    def require_programmatic_provisioning(self) -> None:
        """Keycloak supports programmatic password provisioning."""

    @staticmethod
    async def create_user(
        user_id: str,
        user_info: UserIdentityClaims,
        role_id: Optional[int] = None,
    ) -> User | None:
        """Create a new user.

        Identity-preservation contract (load-bearing for
        ``OrgStore.delete_org_cascade``): both ``Org.id`` and ``User.id``
        are derived from the caller-provided ``user_id`` (the Keycloak
        ``sub`` claim, stable across logins). This means a user whose
        personal org was previously cascade-deleted will be re-onboarded
        here with the **same** ``User.id`` / ``Org.id`` as before,
        restoring the ``User.id == Org.id == UUID(keycloak.sub)``
        invariant that downstream lookups keyed on ``keycloak_user_id``
        depend on.

        If this derivation ever changes (for example, switching to a
        server-generated UUID), the personal-org self-service recovery
        path in ``delete_org_cascade`` step 3a will silently break:
        re-login will succeed but the new IDs will not match the
        deleted-tenant IDs, breaking any external reference that pinned
        on the old values.
        """
        async with a_session_maker() as session:
            user_uuid = uuid.UUID(user_id)
            result = await session.execute(
                select(User)
                .options(selectinload(User.org_members))
                .filter(User.id == user_uuid)
            )
            existing_user: User | None = result.scalars().first()
            if existing_user:
                return existing_user

            # First-user → superadmin: if the caller did not specify a
            # super ``role_id`` and there are no existing users in the
            # database, designate this user as a ``superadmin`` (the
            # ``admin`` role attached via ``user.role_id``). Super-role
            # permissions are explicit in ``server.auth.authorization``
            # and do not inherit org-scoped admin permissions.
            if role_id is None:
                existing_user_count = await session.scalar(
                    select(func.count()).select_from(User)
                )
                if existing_user_count == 0:
                    superadmin_role = await RoleStore.get_role_by_name('admin', session)
                    if superadmin_role is not None:
                        role_id = superadmin_role.id
                        logger.info(
                            'user_store:create_user:first_user_designated_superadmin',
                            extra={'user_id': user_id},
                        )

            org = await session.get(Org, user_uuid)
            org_created = False
            if not org:
                org = Org(
                    id=user_uuid,
                    name=f'user_{user_id}_org',
                    contact_name=resolve_display_name(user_info)
                    or user_info.get('preferred_username', ''),
                    contact_email=user_info['email'],
                    v1_enabled=True,
                )
                session.add(org)
                org_created = True
            else:
                if not org.contact_name:
                    org.contact_name = resolve_display_name(user_info) or user_info.get(
                        'preferred_username', ''
                    )
                if not org.contact_email:
                    org.contact_email = user_info.get('email')

            settings = await UserStore.create_default_settings(
                org_id=str(org.id), user_id=user_id
            )

            if not settings:
                return None

            if org_created:
                from storage.org_store import OrgStore

                org_kwargs = OrgStore.get_kwargs_from_settings(settings)
                for key, value in org_kwargs.items():
                    if hasattr(org, key):
                        setattr(org, key, value)
                org.agent_settings = apply_configured_org_condenser_default(
                    org.agent_settings
                )

            user_kwargs = UserStore.get_kwargs_from_settings(settings)
            user = User(
                id=user_uuid,
                current_org_id=org.id,
                role_id=role_id,
                **user_kwargs,
            )
            user.email = user_info.get('email')
            user.email_verified = user_info.get('email_verified')
            session.add(user)

            role = await RoleStore.get_role_by_name('owner')
            if role is None:
                raise ValueError('Owner role not found in database')

            from storage.org_member_store import OrgMemberStore

            org_member_kwargs = OrgMemberStore.get_kwargs_from_settings(settings)
            org_member = OrgMember(
                org_id=org.id,
                user_id=user.id,
                role_id=role.id,  # owner of your own org.
                status='active',
                **org_member_kwargs,
            )
            session.add(org_member)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                logger.warning(
                    'user_store:create_user:integrity_error',
                    extra={'user_id': user_id},
                )
                result = await session.execute(
                    select(User)
                    .options(selectinload(User.org_members))
                    .filter(User.id == user_uuid)
                )
                existing_user = result.scalars().first()
                if existing_user:
                    return existing_user
                raise
            await session.refresh(user)
            await session.refresh(user, ['org_members'])  # load org_members
            return user

    @staticmethod
    async def migrate_user(
        user_id: str,
        user_settings: UserSettings,
        user_info: UserIdentityClaims,
    ) -> User | None:
        kwargs = decrypt_legacy_model(
            [
                'llm_api_key',
                'llm_api_key_for_byor',
                'search_api_key',
                'sandbox_api_key',
            ],
            user_settings,
        )
        decrypted_user_settings = UserSettings(**kwargs)
        async with a_session_maker() as session:
            # Check if user has completed billing sessions to enable BYOR export
            from storage.billing_session import BillingSession

            result = await session.execute(
                select(BillingSession).filter(
                    BillingSession.user_id == user_id,
                    BillingSession.status == 'completed',
                )
            )
            has_completed_billing = result.scalars().first() is not None

            # create personal org
            org = Org(
                id=uuid.UUID(user_id),
                name=f'user_{user_id}_org',
                org_version=user_settings.user_version,
                contact_name=resolve_display_name(user_info)
                or user_info.get('username', ''),
                contact_email=user_info['email'],
                byor_export_enabled=has_completed_billing,
            )
            session.add(org)

            from storage.lite_llm_manager import LiteLlmManager

            logger.debug(
                'user_store:migrate_user:calling_litellm_migrate_entries',
                extra={'user_id': user_id},
            )
            await LiteLlmManager.migrate_entries(
                str(org.id),
                user_id,
                decrypted_user_settings,
            )

            logger.debug(
                'user_store:migrate_user:done_litellm_migrate_entries',
                extra={'user_id': user_id},
            )
            custom_settings = UserStore._has_custom_settings(
                decrypted_user_settings, user_settings.user_version
            )

            # Migrate stripe customer (pass session to avoid FK violation)
            # avoids circular reference. This migrate method is temporary until all users are migrated.
            from integrations.stripe_service import migrate_customer

            logger.debug(
                'user_store:migrate_user:calling_stripe_migrate_customer',
                extra={'user_id': user_id},
            )
            await migrate_customer(session, user_id, org)
            logger.debug(
                'user_store:migrate_user:done_stripe_migrate_customer',
                extra={'user_id': user_id},
            )

            from storage.org_store import OrgStore

            org_kwargs = UserStore._get_org_kwargs_for_migration(
                decrypted_user_settings,
                custom_settings=custom_settings,
            )

            for key, value in org_kwargs.items():
                if hasattr(org, key):
                    setattr(org, key, value)

            org.agent_settings = apply_configured_org_condenser_default(
                org.agent_settings
            )

            # Apply DEFAULT_V1_ENABLED for migrated orgs if v1_enabled was not set
            if org.v1_enabled is None:
                org.v1_enabled = DEFAULT_V1_ENABLED

            user_kwargs = UserStore.get_kwargs_from_user_settings(
                decrypted_user_settings
            )
            user_kwargs.pop('id', None)
            user = User(
                id=uuid.UUID(user_id),
                current_org_id=org.id,
                role_id=None,
                **user_kwargs,
            )
            session.add(user)

            logger.debug(
                'user_store:migrate_user:calling_get_role_by_name',
                extra={'user_id': user_id},
            )
            role = await RoleStore.get_role_by_name('owner')
            logger.debug(
                'user_store:migrate_user:done_get_role_by_name',
                extra={'user_id': user_id},
            )
            if role is None:
                raise ValueError('Owner role not found in database')

            from storage.org_member_store import OrgMemberStore

            org_member_kwargs = OrgMemberStore.get_kwargs_from_user_settings(
                decrypted_user_settings
            )
            if not custom_settings:
                org_member_kwargs['agent_settings_diff'] = (
                    OrgStore.get_agent_settings_from_org(org).model_dump(mode='json')
                )

            org_member = OrgMember(
                org_id=org.id,
                user_id=user.id,
                role_id=role.id,  # owner of your own org.
                status='active',
                **org_member_kwargs,
            )
            session.add(org_member)

            # Mark the old user_settings as migrated instead of deleting
            user_settings.already_migrated = True
            await session.merge(user_settings)
            await session.flush()
            logger.debug(
                'user_store:migrate_user:session_flush_complete',
                extra={'user_id': user_id},
            )

            user_uuid = uuid.UUID(user_id)

            # need to migrate conversation metadata
            await session.execute(
                text("""
                    INSERT INTO conversation_metadata_saas (conversation_id, user_id, org_id)
                    SELECT
                        conversation_id,
                        :user_uuid,
                        :user_uuid
                    FROM conversation_metadata
                    WHERE user_id = :user_id_text
                """),
                {'user_uuid': user_uuid, 'user_id_text': user_id},
            )

            # Update stripe_customers
            await session.execute(
                text(
                    'UPDATE stripe_customers SET org_id = :org_id WHERE keycloak_user_id = :user_id'
                ),
                {'org_id': user_uuid, 'user_id': user_id},
            )

            # Update slack_users
            await session.execute(
                text(
                    'UPDATE slack_users SET org_id = :org_id WHERE keycloak_user_id = :user_id'
                ),
                {'org_id': user_uuid, 'user_id': user_id},
            )

            # Update slack_conversation
            await session.execute(
                text(
                    'UPDATE slack_conversation SET org_id = :org_id WHERE keycloak_user_id = :user_id'
                ),
                {'org_id': user_uuid, 'user_id': user_id},
            )

            # Update api_keys
            await session.execute(
                text('UPDATE api_keys SET org_id = :org_id WHERE user_id = :user_id'),
                {'org_id': user_uuid, 'user_id': user_id},
            )

            # Update custom_secrets
            await session.execute(
                text(
                    'UPDATE custom_secrets SET org_id = :org_id WHERE keycloak_user_id = :user_id'
                ),
                {'org_id': user_uuid, 'user_id': user_id},
            )

            # Update billing_sessions
            await session.execute(
                text(
                    'UPDATE billing_sessions SET org_id = :org_id WHERE user_id = :user_id'
                ),
                {'org_id': user_uuid, 'user_id': user_id},
            )

            await session.commit()
            await session.refresh(user)
            await session.refresh(user, ['org_members'])  # load org_members
            logger.debug(
                'user_store:migrate_user:session_committed',
                extra={'user_id': user_id},
            )
            return user

    @staticmethod
    async def downgrade_user(user_id: str) -> UserSettings | None:
        """This method can be removed once orgs is established - probably after Feb 15 2026
        Downgrade a migrated user back to the pre-migration state.

        This reverses the migrate_user operation:
        1. Get the user's settings from user_settings table (migrated users) or
           create new user_settings from org_members table (new sign-ups)
        2. Call LiteLlmManager.downgrade_entries to revert LiteLLM state
        3. Copy user_id from conversation_metadata_saas to conversation_metadata
        4. Delete conversation_metadata_saas entries
        5. Reset org_id columns in related tables (stripe_customers, slack_users, etc.)
        6. Delete the org_member and org entries
        7. Delete the user entry
        8. Set already_migrated=False on user_settings

        For new sign-ups (users who registered after migration was deployed),
        there won't be an existing user_settings entry. In this case, we fall back
        to the org_members table to get the user's API keys and settings, and create
        a new user_settings entry for them.

        Args:
            user_id: The Keycloak user ID to downgrade

        Returns:
            The user_settings if downgrade was successful, None otherwise.
            Returns None if the org has multiple members (not a personal org).
        """
        logger.info(
            'user_store:downgrade_user:start',
            extra={'user_id': user_id},
        )

        async with a_session_maker() as session:
            # Get the user and their org_member
            result = await session.execute(
                select(User)
                .options(selectinload(User.org_members))
                .filter(User.id == uuid.UUID(user_id))
            )
            user = result.scalars().first()
            if not user:
                logger.warning(
                    'user_store:downgrade_user:user_not_found',
                    extra={'user_id': user_id},
                )
                return None

            # Get the user's personal org (org_id == user_id)
            org_result = await session.execute(
                select(Org).filter(Org.id == uuid.UUID(user_id))
            )
            org = org_result.scalars().first()
            if not org:
                logger.warning(
                    'user_store:downgrade_user:org_not_found',
                    extra={'user_id': user_id},
                )
                return None

            # Get org_members for this org - should only be one for personal orgs
            members_result = await session.execute(
                select(OrgMember).filter(OrgMember.org_id == org.id)
            )
            org_members = members_result.scalars().all()

            if len(org_members) != 1:
                logger.error(
                    'user_store:downgrade_user:unexpected_org_members_count',
                    extra={
                        'user_id': user_id,
                        'org_id': str(org.id),
                        'org_members_count': len(org_members),
                    },
                )
                return None

            org_member = org_members[0]

            # Get the user_settings (for migrated users)
            settings_result = await session.execute(
                select(UserSettings).filter(
                    UserSettings.keycloak_user_id == user_id,
                    UserSettings.already_migrated.is_(True),
                )
            )
            user_settings: UserSettings | None = settings_result.scalars().first()

            # For new sign-ups after migration, user_settings won't exist
            # Fall back to getting data from org_members
            if user_settings:
                UserStore._sync_user_settings_from_org_member(user_settings, org_member)
                logger.info(
                    'user_store:downgrade_user:updated_user_settings_from_org_member',
                    extra={'user_id': user_id},
                )
            else:
                # Create a new user_settings entry from OrgMember, User, and Org data
                # This is needed for new sign-ups who don't have user_settings
                user_settings = UserStore._create_user_settings_from_entities(
                    user_id, org_member, user, org
                )
                session.add(user_settings)
                logger.info(
                    'user_store:downgrade_user:created_user_settings_from_org_member',
                    extra={'user_id': user_id},
                )
            await session.flush()

            # Call LiteLLM downgrade
            from storage.lite_llm_manager import LiteLlmManager

            logger.debug(
                'user_store:downgrade_user:calling_litellm_downgrade_entries',
                extra={'user_id': user_id},
            )

            encrypted_fields = [
                'llm_api_key',
                'llm_api_key_for_byor',
                'search_api_key',
                'sandbox_api_key',
            ]
            for field in encrypted_fields:
                value = getattr(user_settings, field, None)
                if value:
                    try:
                        value = decrypt_legacy_value(value)
                        setattr(user_settings, field, value)
                    except Exception:
                        pass

            await LiteLlmManager.downgrade_entries(
                str(org.id),
                user_id,
                user_settings,
            )
            logger.debug(
                'user_store:downgrade_user:done_litellm_downgrade_entries',
                extra={'user_id': user_id},
            )

            user_uuid = uuid.UUID(user_id)

            # Step 3: Copy user_id from conversation_metadata_saas to conversation_metadata
            # This ensures any conversations created after migration have their user_id
            # preserved in the original table before we delete the saas entries
            await session.execute(
                text("""
                    UPDATE conversation_metadata
                    SET user_id = :user_id
                    WHERE conversation_id IN (
                        SELECT conversation_id
                        FROM conversation_metadata_saas
                        WHERE user_id = :user_uuid
                    )
                """),
                {'user_id': user_id, 'user_uuid': user_uuid},
            )

            # Step 4: Delete conversation_metadata_saas entries
            await session.execute(
                text('DELETE FROM conversation_metadata_saas WHERE user_id = :user_id'),
                {'user_id': user_uuid},
            )

            # Step 5: Reset org_id columns in related tables
            # Reset stripe_customers
            await session.execute(
                text(
                    'UPDATE stripe_customers SET org_id = NULL WHERE org_id = :org_id'
                ),
                {'org_id': user_uuid},
            )

            # Reset slack_users
            await session.execute(
                text('UPDATE slack_users SET org_id = NULL WHERE org_id = :org_id'),
                {'org_id': user_uuid},
            )

            # Reset slack_conversation
            await session.execute(
                text(
                    'UPDATE slack_conversation SET org_id = NULL WHERE org_id = :org_id'
                ),
                {'org_id': user_uuid},
            )

            # Reset api_keys
            await session.execute(
                text('UPDATE api_keys SET org_id = NULL WHERE org_id = :org_id'),
                {'org_id': user_uuid},
            )

            # Reset custom_secrets
            await session.execute(
                text('UPDATE custom_secrets SET org_id = NULL WHERE org_id = :org_id'),
                {'org_id': user_uuid},
            )

            # Reset billing_sessions
            await session.execute(
                text(
                    'UPDATE billing_sessions SET org_id = NULL WHERE org_id = :org_id'
                ),
                {'org_id': user_uuid},
            )

            # Step 6: Delete org_member entries for this org
            await session.execute(
                text('DELETE FROM org_member WHERE org_id = :org_id'),
                {'org_id': user_uuid},
            )

            # Step 7: Delete the user entry
            await session.execute(
                text('DELETE FROM "user" WHERE id = :user_id'),
                {'user_id': user_uuid},
            )

            # Delete the org entry
            await session.execute(
                text('DELETE FROM org WHERE id = :org_id'),
                {'org_id': user_uuid},
            )

            # Step 8: Set already_migrated=False on user_settings and encrypt fields
            user_settings.already_migrated = False

            # Re-encrypt the sensitive fields before storing in the DB
            encrypt_keys = [
                'llm_api_key',
                'llm_api_key_for_byor',
                'search_api_key',
                'sandbox_api_key',
            ]
            for key in encrypt_keys:
                value = getattr(user_settings, key, None)
                if value is not None and not _is_legacy_value_encrypted(value):
                    setattr(user_settings, key, encrypt_legacy_value(value))

            await session.merge(user_settings)

            await session.commit()

            logger.info(
                'user_store:downgrade_user:complete',
                extra={'user_id': user_id},
            )
            return user_settings
