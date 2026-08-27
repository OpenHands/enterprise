"""Instance-level user lifecycle operations for Enterprise administrators."""

from dataclasses import dataclass, field
from uuid import UUID

import httpx

from server.auth.token_manager import TokenManager
from sqlalchemy import text, update
from storage.database import a_session_maker
from storage.lite_llm_manager import LiteLlmManager
from storage.offline_token_store import OfflineTokenStore
from storage.org_store import OrgStore
from storage.user import User
from storage.user_store import UserStore

from openhands.app_server.utils.logger import openhands_logger as logger


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


class AdminUserLifecycleService:
    """Coordinate user state across Enterprise and external identity systems."""

    def __init__(self, token_manager: TokenManager | None = None):
        self.token_manager = token_manager or TokenManager()

    async def get_user(self, user_id: str) -> User | None:
        """Return a user or ``None`` for a missing identity."""
        try:
            UUID(user_id)
        except ValueError:
            return None
        return await UserStore.get_user_by_id(user_id)

    async def disable_user(self, user_id: str) -> UserLifecycleResult | None:
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

    async def enable_user(self, user_id: str) -> UserLifecycleResult | None:
        """Re-enable a locally and externally disabled identity."""
        user = await self.get_user(user_id)
        if user is None:
            return None

        await self.token_manager.enable_keycloak_user(user_id, user.email)
        await self._set_disabled(user_id, False)
        logger.info('admin_user_lifecycle:enabled', extra={'user_id': user_id})
        return UserLifecycleResult(user_id=user_id, email=user.email)

    async def delete_user(self, user_id: str) -> UserDeletionResult | None:
        """Delete all Enterprise-owned data and the external identity.

        Local database deletion is attempted before the Keycloak identity
        is removed, so retrying can reconcile any failed local cleanup.
        External
        cleanup (Keycloak / LiteLLM) is best-effort and reported as warnings
        so an operator can reconcile any credential that could not be
        revoked before the account disappears.
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
        if any(admin.id == user.id for admin in superadmins):
            active = [admin for admin in superadmins if not admin.is_disabled]
            if len(active) <= 1:
                raise LastSuperAdminError(
                    'Cannot disable or delete the last active superadmin'
                )

    async def _set_disabled(self, user_id: str, disabled: bool) -> None:
        async with a_session_maker() as session:
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

    async def _delete_user_data(self, user_id: str) -> None:
        user = await UserStore.get_user_by_id(user_id)
        if user is None:
            return

        user_uuid = user.id
        await OrgStore.delete_org_cascade(user_uuid, requester_user_id=user_id)

        user_id_str = str(user_id)
        user_uuid_str = str(user_uuid)
        async with a_session_maker() as session:
            # Direct user-owned rows that are not guaranteed to live in the
            # personal workspace org being cascade-deleted above. Rows owned
            # by the personal org (api_keys, conversation_metadata_saas,
            # slack_*, billing, for that org) are already removed by
            # ``delete_org_cascade``; these cover shared-org and
            # identity-level leftovers deterministically. Conversation
            # metadata for shared-org conversations is removed by user_id
            # after the cascade (which already handled the personal org),
            # without sweeping unrelated conversations.
            await session.execute(
                text("""
                    DELETE FROM conversation_metadata
                    WHERE conversation_id IN (
                        SELECT conversation_id FROM conversation_metadata_saas
                        WHERE user_id = :uuid
                    )
                """),
                {'uuid': user_uuid_str},
            )
            await session.execute(
                text("""
                    DELETE FROM app_conversation_start_task
                    WHERE app_conversation_id IN (
                        SELECT conversation_id::uuid FROM conversation_metadata_saas
                        WHERE user_id = :uuid
                    )
                """),
                {'uuid': user_uuid_str},
            )
            statements = (
                'DELETE FROM conversation_metadata_saas WHERE user_id = :uuid',
                'DELETE FROM conversation_work WHERE user_id = :uid',
                'DELETE FROM app_conversation_start_task WHERE created_by_user_id = :uid',
                'DELETE FROM jira_workspaces WHERE admin_user_id = :uid',
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
                'DELETE FROM github_app_installations WHERE user_id = :uid',
                'DELETE FROM org_git_claim WHERE claimed_by = :uuid',
                'DELETE FROM org_invitation WHERE inviter_id = :uuid OR accepted_by_user_id = :uuid',
                'DELETE FROM org_user_budget_override WHERE user_id = :uuid',
                'DELETE FROM org_member WHERE user_id = :uuid',
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
                    {'uid': user_id_str, 'uuid': user_uuid_str},
                )
            await session.execute(
                text('DELETE FROM "user" WHERE id = :uuid'),
                {'uuid': user_uuid_str},
            )
            await session.commit()


__all__ = [
    'AdminUserLifecycleService',
    'LastSuperAdminError',
    'UserLifecycleResult',
    'UserDeletionResult',
]
