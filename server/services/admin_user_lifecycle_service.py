"""Instance-level user lifecycle operations for Enterprise administrators."""

from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

import httpx
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.native_password import NativeAuthError
from server.auth.token_manager import TokenManager
from storage.database import a_session_maker
from storage.lite_llm_manager import LiteLlmManager
from storage.offline_token_store import OfflineTokenStore
from storage.org_store import OrgStore
from storage.user import User
from storage.user_store import SuperAdminRevokeResult, UserStore


class LastSuperAdminError(RuntimeError):
    """Raised when an operation would remove the final active superadmin."""


@dataclass(frozen=True)
class UserLifecycleResult:
    """Summary of a lifecycle operation."""

    user_id: str
    email: str | None


@dataclass(frozen=True)
class UserDeletionResult(UserLifecycleResult):
    """Result of a user deletion, including best-effort cleanup notes."""

    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def cleanup_warnings(self) -> list[str]:
        return list(self.notes)


class LifecycleOperations(Protocol):
    async def run_maintenance(self) -> dict[str, int]: ...
    async def disable_user(
        self, user_id: str, *, actor_user_id: str
    ) -> UserLifecycleResult | None: ...
    async def enable_user(
        self, user_id: str, *, actor_user_id: str
    ) -> UserLifecycleResult | None: ...
    async def delete_user(
        self, user_id: str, *, actor_user_id: str
    ) -> UserDeletionResult | None: ...
    async def grant_super_admin(
        self, user_id: str, *, actor_user_id: str
    ) -> User | None: ...
    async def revoke_super_admin(
        self, user_id: str, *, actor_user_id: str
    ) -> SuperAdminRevokeResult: ...
    async def lock_mutation(self, session: AsyncSession) -> None: ...
    async def mark_org_orphans(
        self,
        session: AsyncSession,
        orphan_ids: list[str],
        terminal_account_id: UUID | None,
    ) -> None: ...


class _UserDataDeletion:
    async def lock_mutation(self, session: AsyncSession) -> None:
        """Acquire any installation-wide lifecycle lock before row locks."""

    async def _delete_user_data(self, user_id: str) -> None:
        user_uuid = UUID(user_id)
        await OrgStore.delete_org_cascade(
            user_uuid, requester_user_id=user_id, terminal_account_id=user_uuid
        )
        user_id_str = str(user_uuid)
        async with a_session_maker() as session:
            await self.lock_mutation(session)
            # Personal org was cascade-deleted above; these DELETEs cover
            # identity-level rows and shared-org leftovers.
            await session.execute(
                text("""
                    DELETE FROM conversation_metadata
                    WHERE conversation_id IN (
                        SELECT conversation_id FROM conversation_metadata_saas
                        WHERE user_id = :uid
                    )
                """),
                {'uid': user_id_str},
            )
            await session.execute(
                text("""
                    DELETE FROM app_conversation_start_task
                    WHERE app_conversation_id IN (
                        SELECT CAST(conversation_id AS UUID)
                        FROM conversation_metadata_saas
                        WHERE user_id = :uid
                    )
                """),
                {'uid': user_id_str},
            )
            statements = (
                'DELETE FROM jira_conversations WHERE jira_user_id IN (SELECT id FROM jira_users WHERE keycloak_user_id = :uid)',
                'DELETE FROM jira_dc_conversations WHERE jira_dc_user_id IN (SELECT id FROM jira_dc_users WHERE keycloak_user_id = :uid)',
                'DELETE FROM linear_conversations WHERE linear_user_id IN (SELECT id FROM linear_users WHERE keycloak_user_id = :uid)',
                'DELETE FROM conversation_metadata_saas WHERE user_id = :uid',
                'DELETE FROM daily_conversation_usage WHERE user_id = :uid',
                'UPDATE quota_increase_request SET approved_by_user_id = NULL WHERE approved_by_user_id = :uid',
                'DELETE FROM quota_increase_request WHERE user_id = :uid',
                'DELETE FROM conversation_work WHERE user_id = :uid',
                'DELETE FROM app_conversation_start_task WHERE created_by_user_id = :uid',
                'DELETE FROM jira_workspaces WHERE admin_user_id = :uid',
                'DELETE FROM jira_dc_workspaces WHERE admin_user_id = :uid',
                'DELETE FROM linear_workspaces WHERE admin_user_id = :uid',
                'DELETE FROM feature_flag_rules WHERE user_id = :uid',
                'DELETE FROM user_settings WHERE keycloak_user_id = :uid',
                'DELETE FROM api_keys WHERE user_id = :uid',
                'DELETE FROM offline_tokens WHERE user_id = :uid',
                'DELETE FROM auth_tokens WHERE keycloak_user_id = :uid',
                'DELETE FROM device_codes WHERE keycloak_user_id = :uid',
                'DELETE FROM "user-repos" WHERE user_id = :uid',
                'DELETE FROM billing_sessions WHERE user_id = :uid',
                'DELETE FROM stripe_customers WHERE keycloak_user_id = :uid',
                'DELETE FROM subscription_access WHERE user_id = :uid',
                'DELETE FROM custom_secrets WHERE keycloak_user_id = :uid',
                'DELETE FROM slack_conversation WHERE keycloak_user_id = :uid',
                'DELETE FROM slack_users WHERE keycloak_user_id = :uid',
                'DELETE FROM resend_synced_users WHERE keycloak_user_id = :uid',
                'DELETE FROM org_git_claim WHERE claimed_by = :uid',
                'DELETE FROM org_invitation WHERE inviter_id = :uid OR accepted_by_user_id = :uid',
                'DELETE FROM org_user_budget_override WHERE user_id = :uid',
                'DELETE FROM org_member WHERE user_id = :uid',
                'DELETE FROM jira_users WHERE keycloak_user_id = :uid',
                'DELETE FROM jira_dc_users WHERE keycloak_user_id = :uid',
                'DELETE FROM linear_users WHERE keycloak_user_id = :uid',
                'DELETE FROM bitbucket_webhook WHERE user_id = :uid',
                'DELETE FROM bitbucket_dc_webhook WHERE user_id = :uid',
                'DELETE FROM gitlab_webhook WHERE user_id = :uid',
            )
            for statement in statements:
                await session.execute(
                    text(statement),
                    {'uid': user_id_str},
                )
            await session.execute(
                text('DELETE FROM "user" WHERE id = :uid'),
                {'uid': user_id_str},
            )
            await session.commit()


class KeycloakUserLifecycleService(_UserDataDeletion):
    async def run_maintenance(self) -> dict[str, int]:
        return {}

    def __init__(self, token_manager: TokenManager | None = None) -> None:
        self.token_manager = token_manager or TokenManager()

    async def get_user(self, user_id: str) -> User | None:
        """Return a user or ``None`` for a missing identity."""
        try:
            UUID(user_id)
        except ValueError:
            return None
        from server.auth.composition import get_auth_services

        return await get_auth_services().accounts.get_user_by_id(user_id)

    async def disable_user(
        self, user_id: str, *, actor_user_id: str
    ) -> UserLifecycleResult | None:
        """Disable the identity and invalidate all credentials without deleting data."""
        user = await self.get_user(user_id)
        if user is None:
            return None

        await self._ensure_not_last_active_superadmin(user)
        await self._set_disabled(user_id, True)
        await self.token_manager.disable_keycloak_user(user_id, user.email)
        await self._delete_api_keys(user_id)
        await self._delete_offline_token(user_id)
        logger.info('admin_user_lifecycle:disabled', extra={'user_id': user_id})
        return UserLifecycleResult(user_id=user_id, email=user.email)

    async def enable_user(
        self, user_id: str, *, actor_user_id: str
    ) -> UserLifecycleResult | None:
        """Re-enable a locally and externally disabled identity."""
        user = await self.get_user(user_id)
        if user is None:
            return None

        await self._set_disabled(user_id, False)
        await self.token_manager.enable_keycloak_user(user_id, user.email)
        logger.info('admin_user_lifecycle:enabled', extra={'user_id': user_id})
        return UserLifecycleResult(user_id=user_id, email=user.email)

    async def delete_user(
        self, user_id: str, *, actor_user_id: str
    ) -> UserDeletionResult | None:
        """Delete all Enterprise-owned data and the external identity.

        Local database deletion is attempted before the Keycloak identity
        is removed, so retrying can reconcile any failed local cleanup.
        External cleanup failures are returned as warnings for reconciliation.
        """
        user = await self.get_user(user_id)
        if user is None:
            return None

        await self._ensure_not_last_active_superadmin(user)
        await self._set_disabled(user_id, True)
        await self.token_manager.disable_keycloak_user(user_id, user.email)
        await self._delete_api_keys(user_id)
        await self._delete_offline_token(user_id)
        await self._delete_user_data(user_id)

        warnings: list[str] = []
        try:
            await LiteLlmManager.delete_user(user_id)
        except httpx.HTTPError as exc:
            warnings.append(f'LiteLLM cleanup failed: {exc}')
            logger.warning(
                'admin_user_lifecycle:litellm_cleanup_failed',
                extra={'user_id': user_id},
            )
        if not await self.token_manager.delete_keycloak_user(user_id):
            warnings.append('Keycloak deletion failed or user already absent')

        logger.info('admin_user_lifecycle:deleted', extra={'user_id': user_id})
        return UserDeletionResult(
            user_id=user_id, email=user.email, notes=tuple(warnings)
        )

    async def _ensure_not_last_active_superadmin(self, user: User) -> None:
        if user.role_id is None:
            return

        superadmins = await UserStore.list_super_admins()
        if user.is_disabled:
            return
        if any(admin.id == user.id for admin in superadmins):
            active = [admin for admin in superadmins if not admin.is_disabled]
            if len(active) <= 1:
                raise LastSuperAdminError(
                    'Cannot disable or delete the last active superadmin'
                )

    async def _set_disabled(self, user_id: str, disabled: bool) -> None:
        async with a_session_maker() as session:
            if disabled:
                active_admins = await session.execute(
                    text("""
                        SELECT u.id
                        FROM "user" u
                        JOIN role r ON r.id = u.role_id
                        WHERE r.name = 'admin' AND u.is_disabled = false
                        FOR UPDATE OF u
                    """)
                )
                active_admin_ids = {str(row[0]) for row in active_admins}
                if user_id in active_admin_ids and len(active_admin_ids) <= 1:
                    raise LastSuperAdminError(
                        'Cannot disable or delete the last active superadmin'
                    )
            await session.execute(
                update(User)
                .where(User.id == UUID(user_id))
                .values(is_disabled=disabled)
            )
            await session.commit()

    async def _delete_api_keys(self, user_id: str) -> None:
        async with a_session_maker() as session:
            await session.execute(
                text('DELETE FROM api_keys WHERE user_id = :uid'), {'uid': user_id}
            )
            await session.commit()

    async def _delete_offline_token(self, user_id: str) -> None:
        token_store = await OfflineTokenStore.get_instance(user_id)
        await token_store.delete_token()

    async def grant_super_admin(
        self, user_id: str, *, actor_user_id: str
    ) -> User | None:
        return await UserStore.grant_super_admin(user_id)

    async def revoke_super_admin(
        self, user_id: str, *, actor_user_id: str
    ) -> SuperAdminRevokeResult:
        return await UserStore.revoke_super_admin(user_id)

    async def mark_org_orphans(
        self,
        session: AsyncSession,
        orphan_ids: list[str],
        terminal_account_id: UUID | None,
    ) -> None:
        """Keycloak identities survive personal-profile deletion."""


class OpenHandsUserLifecycleService(_UserDataDeletion):
    async def lock_mutation(self, session: AsyncSession) -> None:
        from server.services.native_account_service import lock_native_lifecycle

        await lock_native_lifecycle(session)

    async def disable_user(
        self, user_id: str, *, actor_user_id: str
    ) -> UserLifecycleResult | None:
        return await self._native_mutation(user_id, actor_user_id, 'disable')

    async def enable_user(
        self, user_id: str, *, actor_user_id: str
    ) -> UserLifecycleResult | None:
        return await self._native_mutation(user_id, actor_user_id, 'enable')

    async def delete_user(
        self, user_id: str, *, actor_user_id: str
    ) -> UserDeletionResult | None:
        result = await self._native_mutation(user_id, actor_user_id, 'delete')
        if result is None:
            return None
        warnings: list[str] = []
        try:
            await self._finish_native_deletion(UUID(user_id))
        except Exception:
            warnings.append('Account access revoked; data cleanup is pending')
        return UserDeletionResult(
            user_id=user_id, email=result.email, notes=tuple(warnings)
        )

    async def grant_super_admin(
        self, user_id: str, *, actor_user_id: str
    ) -> User | None:
        from server.services.native_account_service import (
            require_active_admin,
            set_superadmin,
        )

        async with a_session_maker() as session, session.begin():
            await self.lock_mutation(session)
            await require_active_admin(session, UUID(actor_user_id))
            await set_superadmin(session, UUID(user_id), True)
            user: User | None = await session.get(User, UUID(user_id))
            return user

    async def revoke_super_admin(
        self, user_id: str, *, actor_user_id: str
    ) -> SuperAdminRevokeResult:
        from server.services.native_account_service import (
            require_active_admin,
            set_superadmin,
        )

        async with a_session_maker() as session, session.begin():
            await self.lock_mutation(session)
            await require_active_admin(session, UUID(actor_user_id))
            target = await session.get(User, UUID(user_id))
            if target is None:
                return SuperAdminRevokeResult.NOT_FOUND
            if target.role_id != await UserStore._get_super_admin_role_id(session):
                return SuperAdminRevokeResult.NOT_SUPER_ADMIN
            try:
                await set_superadmin(session, target.id, False)
            except NativeAuthError as exc:
                if exc.status_code == 409:
                    return SuperAdminRevokeResult.LAST_SUPER_ADMIN
                raise
            return SuperAdminRevokeResult.REVOKED

    async def mark_org_orphans(
        self,
        session: AsyncSession,
        orphan_ids: list[str],
        terminal_account_id: UUID | None,
    ) -> None:
        from server.services.native_account_service import mark_self_deleted
        from storage.api_key import ApiKey
        from storage.native_auth import AuthAccount

        for user_id in orphan_ids:
            account_id = UUID(user_id)
            account = await session.get(AuthAccount, account_id)
            if (
                terminal_account_id != account_id
                or account is None
                or account.state != 'deleted'
            ):
                await mark_self_deleted(session, account_id)
            await session.execute(delete(ApiKey).where(ApiKey.user_id == user_id))

    async def _native_mutation(
        self,
        user_id: str,
        actor_user_id: str,
        operation: Literal['enable', 'disable', 'delete'],
    ) -> UserLifecycleResult | None:
        from server.services.native_account_service import (
            lock_native_lifecycle,
            require_active_admin,
            set_account_enabled,
            tombstone_account,
        )
        from server.services.native_provisioning_service import queue_external_cleanup
        from storage.api_key import ApiKey
        from storage.native_auth import AuthAccount

        try:
            account_id = UUID(user_id)
        except ValueError:
            return None
        async with a_session_maker() as session, session.begin():
            await lock_native_lifecycle(session)
            await require_active_admin(session, UUID(actor_user_id))
            account = await session.get(AuthAccount, account_id)
            if account is None:
                return None
            email = account.display_email
            if operation == 'delete':
                await tombstone_account(session, account_id)
                account.provisioning_status = 'cleanup_pending'
                await queue_external_cleanup(session, account_id=account_id)
            else:
                await set_account_enabled(session, account_id, operation == 'enable')
            if operation != 'enable':
                await session.execute(delete(ApiKey).where(ApiKey.user_id == user_id))
                await session.execute(
                    text('DELETE FROM offline_tokens WHERE user_id = :uid'),
                    {'uid': user_id},
                )
            return UserLifecycleResult(user_id=user_id, email=email)

    async def _finish_native_deletion(self, account_id: UUID) -> None:
        from server.services.native_account_service import lock_native_lifecycle
        from storage.native_auth import AuthAccount

        await self._delete_user_data(str(account_id))
        async with a_session_maker() as session, session.begin():
            await lock_native_lifecycle(session)
            account = await session.get(AuthAccount, account_id)
            if account is not None and account.state == 'deleted':
                account.provisioning_status = 'complete'

    async def retry_native_deletions(self) -> int:
        from storage.native_auth import AuthAccount

        async with a_session_maker() as session:
            ids = list(
                await session.scalars(
                    select(AuthAccount.id)
                    .where(
                        AuthAccount.state == 'deleted',
                        AuthAccount.provisioning_status == 'cleanup_pending',
                    )
                    .limit(100)
                )
            )
        failures = 0
        for account_id in ids:
            try:
                await self._finish_native_deletion(account_id)
            except Exception:
                failures += 1
        return failures

    async def run_maintenance(self) -> dict[str, int]:
        from server.services.native_auth_service import get_native_auth_service
        from server.services.native_enrollment_service import (
            get_native_enrollment_service,
        )
        from server.services.native_provisioning_service import (
            NativeProvisioningService,
        )

        await get_native_auth_service().cleanup_expired_state()
        await get_native_enrollment_service().cleanup_expired_state()
        failures = await self.retry_native_deletions()
        reconciler = NativeProvisioningService()
        cleaned, cleanup_failed = await reconciler.cleanup()
        provisioned, provisioning_failed = await reconciler.reconcile()
        return {
            'cleaned': cleaned,
            'provisioned': provisioned,
            'error_count': failures + cleanup_failed + provisioning_failed,
        }
