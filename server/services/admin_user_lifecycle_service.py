"""Instance account lifecycle, with local revocation preceding remote cleanup."""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from uuid import UUID

from sqlalchemy import delete, select, text

from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.mode import SessionFactory, is_keycloak_enabled
from storage.api_key import ApiKey
from storage.auth_action_tokens import AuthActionToken
from storage.auth_sessions import AuthSession
from storage.database import a_session_maker
from storage.lite_llm_manager import LiteLlmManager
from storage.local_credentials import LocalCredentials
from storage.org_store import OrgStore
from storage.stored_offline_token import StoredOfflineToken
from storage.user import User
from storage.user_store import UserStore


class LastSuperAdminError(RuntimeError):
    """An operation would remove the final active instance administrator."""


@dataclass(frozen=True)
class UserLifecycleResult:
    user_id: str
    email: str | None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def cleanup_warnings(self) -> list[str]:
        return list(self.notes)


@dataclass(frozen=True)
class UserDeletionResult(UserLifecycleResult):
    pass


class AdminUserLifecycleService:
    def __init__(
        self, token_manager=None, *, session_factory: SessionFactory | None = None
    ):
        self.token_manager = token_manager
        self.session_factory = session_factory or a_session_maker

    def _keycloak(self):
        from server.auth.keycloak.account_management import KeycloakAccountManagement

        return KeycloakAccountManagement(self.token_manager)

    @asynccontextmanager
    async def _lifecycle_lock(self, user_id: str):
        """Serialize disable/enable/delete across their committed denial boundary.

        A separate, read-only PostgreSQL transaction holds a per-account advisory
        lock until remote reconciliation and final local writes finish. Inner
        transactions can commit revocation before calling the remote provider.
        Transaction locks are released even when requests fail or are cancelled.
        """
        async with self.session_factory() as session, session.begin():
            if session.bind.dialect.name == 'postgresql':
                key = int.from_bytes(
                    sha256(b'account-lifecycle:' + UUID(user_id).bytes).digest()[:8],
                    'big',
                    signed=True,
                )
                await session.execute(
                    text('SELECT pg_advisory_xact_lock(:key)'), {'key': key}
                )
            yield

    async def get_user(self, user_id: str) -> User | None:
        try:
            identifier = UUID(user_id)
        except ValueError:
            return None
        async with self.session_factory() as session:
            return await session.get(User, identifier)

    async def _deny_and_revoke(self, user_id: str) -> User | None:
        """Role mutex -> User -> credential; commit denial and all revocation together."""
        async with self.session_factory() as session, session.begin():
            admin_role = await UserStore.lock_super_admin_policy(session)
            user = await session.scalar(
                select(User).where(User.id == UUID(user_id)).with_for_update()
            )
            if user is None:
                return None
            if await UserStore.is_last_active_admin(session, user, admin_role):
                raise LastSuperAdminError(
                    'Cannot disable or delete the last active superadmin'
                )
            await session.scalar(
                select(LocalCredentials)
                .where(LocalCredentials.user_id == user.id)
                .with_for_update()
            )
            user.is_disabled = True
            await session.execute(
                delete(AuthSession).where(AuthSession.user_id == user.id)
            )
            await session.execute(
                delete(AuthActionToken).where(AuthActionToken.user_id == user.id)
            )
            await session.execute(delete(ApiKey).where(ApiKey.user_id == user_id))
            await session.execute(
                delete(StoredOfflineToken).where(StoredOfflineToken.user_id == user_id)
            )
            return user

    async def disable_user(self, user_id: str) -> UserLifecycleResult | None:
        async with self._lifecycle_lock(user_id):
            return await self._disable_user(user_id)

    async def _disable_user(self, user_id: str) -> UserLifecycleResult | None:
        user = await self._deny_and_revoke(user_id)
        if user is None:
            return None
        notes = []
        if is_keycloak_enabled():
            try:
                await self._keycloak().set_enabled(user_id, False)
            except Exception:
                notes.append('Keycloak disable is pending. Retry this operation.')
                logger.warning(
                    'admin_user_lifecycle:remote_disable_pending',
                    extra={'user_id': user_id},
                )
        return UserLifecycleResult(user_id, user.email, tuple(notes))

    async def enable_user(self, user_id: str) -> UserLifecycleResult | None:
        async with self._lifecycle_lock(user_id):
            return await self._enable_user(user_id)

    async def _enable_user(self, user_id: str) -> UserLifecycleResult | None:
        # Keep local denial until upstream enable succeeds. Holding the User lock
        # serializes concurrent enable/disable and password/session issuance.
        async with self.session_factory() as session, session.begin():
            await UserStore.lock_super_admin_policy(session)
            user = await session.scalar(
                select(User).where(User.id == UUID(user_id)).with_for_update()
            )
            if user is None:
                return None
            if is_keycloak_enabled():
                await self._keycloak().set_enabled(user_id, True)
            user.is_disabled = False
            return UserLifecycleResult(user_id, user.email)

    async def delete_user(self, user_id: str) -> UserDeletionResult | None:
        async with self._lifecycle_lock(user_id):
            return await self._delete_user(user_id)

    async def _delete_user(self, user_id: str) -> UserDeletionResult | None:
        """Keep a disabled reconciliation identity until remote cleanup succeeds.

        The same DELETE retries pending cleanup. A warning means deletion is
        pending, with access already revoked. No queue or additional identity
        registry is needed, and remote errors never include provider payloads.
        """
        user = await self._deny_and_revoke(user_id)
        if user is None:
            return None
        notes = []
        if is_keycloak_enabled():
            try:
                await self._keycloak().delete(user_id)
            except Exception:
                notes.append('Keycloak deletion is pending. Retry this operation.')
        try:
            await LiteLlmManager.delete_user(user_id)
            await LiteLlmManager.delete_team(user_id)
        except Exception:
            notes.append('LiteLLM deletion is pending. Retry this operation.')
        if notes:
            logger.warning(
                'admin_user_lifecycle:deletion_pending', extra={'user_id': user_id}
            )
            return UserDeletionResult(user_id, user.email, tuple(notes))
        await self._delete_user_data(user_id)
        return UserDeletionResult(user_id, user.email)

    async def _delete_user_data(self, user_id: str) -> None:
        user = await UserStore.get_user_by_id(user_id)
        if user is None:
            return

        user_uuid = user.id
        await OrgStore.delete_org_cascade(
            user_uuid, requester_user_id=user_id, delete_account=True
        )

        user_id_str = str(user_uuid)
        async with self.session_factory() as session:
            admin_role_id = await UserStore.lock_super_admin_policy(session)
            remaining = await session.scalar(
                select(User).where(User.id == user_uuid).with_for_update()
            )
            if remaining is not None:
                if await UserStore.is_last_active_admin(
                    session, remaining, admin_role_id
                ):
                    raise LastSuperAdminError(
                        'Cannot delete the last active superadmin'
                    )
                remaining.is_disabled = True
                # Flush ORM state before raw SQL removes this identity.
                await session.flush()
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


__all__ = [
    'AdminUserLifecycleService',
    'LastSuperAdminError',
    'UserLifecycleResult',
    'UserDeletionResult',
]
