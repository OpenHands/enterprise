"""Retryable instance-level user lifecycle operations."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text

from openhands.app_server.sandbox.sandbox_store import StoredSandbox
from server.logger import logger
from server.services.user_lifecycle_remote import UserLifecycleRemote
from storage.database import a_session_maker
from storage.org_store import OrgStore
from storage.role import Role
from storage.user import ACCOUNT_TYPE_SERVICE, User
from storage.user_data_cleanup import (
    UserCleanupConflict,
    check_user_data_cleanup,
    delete_user_data,
)
from storage.user_store import USER_LIFECYCLE_LOCK_NAMESPACE


class LastSuperAdminError(RuntimeError):
    pass


class LifecycleCleanupError(RuntimeError):
    pass


class ServiceAccountDeleteError(RuntimeError):
    pass


@dataclass(frozen=True)
class UserLifecycleResult:
    user_id: str
    email: str | None


class AdminUserLifecycleService:
    def __init__(self, session_factory=None, remote=None):
        self.session_factory = session_factory or a_session_maker
        self.remote = remote or UserLifecycleRemote()

    async def disable_user(self, user_id: str):
        return await self._operate(user_id, 'disable')

    async def enable_user(self, user_id: str):
        return await self._operate(user_id, 'enable')

    async def delete_user(self, user_id: str):
        return await self._operate(user_id, 'delete')

    async def delete_service_account(self, user_id: str):
        """Delete a service account. Refuses any user that is not one.

        The only path that deletes a service account; ``delete_user`` refuses
        them. The caller must have checked that it owns no automation. Its
        sandboxes, paused ones included, are deleted first: a service account
        cannot clear them itself, and they would block the cleanup check.

        Raises:
            ValueError: If the user is not a service account.
            LifecycleCleanupError: If a sandbox could not be deleted. Retry.
        """
        await self._delete_service_account_sandboxes(UUID(user_id))
        return await self._operate(user_id, 'delete', service_account=True)

    async def _delete_service_account_sandboxes(self, uid: UUID) -> None:
        """Delete every sandbox the service account created.

        Does nothing for a user that is not a service account. Each sandbox
        is deleted and committed on its own, so a failure keeps the ones that
        are already gone. The sandbox service is scoped to this one user.
        A sandbox it cannot see is logged and skipped; the cleanup check
        then blocks the delete.
        """
        # Local imports to avoid circular dependencies.
        from openhands.app_server.config import get_sandbox_service
        from openhands.app_server.services.injector import InjectorState
        from openhands.app_server.user.specifiy_user_context import (
            USER_CONTEXT_ATTR,
            SpecifyUserContext,
        )

        async with self.session_factory() as session:
            account_type = await session.scalar(
                select(User.account_type).where(User.id == uid)
            )
            if account_type != ACCOUNT_TYPE_SERVICE:
                return
            sandbox_ids = (
                await session.scalars(
                    select(StoredSandbox.id).where(
                        StoredSandbox.created_by_user_id == str(uid)
                    )
                )
            ).all()
        for sandbox_id in sandbox_ids:
            state = InjectorState()
            setattr(state, USER_CONTEXT_ATTR, SpecifyUserContext(user_id=str(uid)))
            try:
                async with get_sandbox_service(state) as sandbox_service:
                    deleted = await sandbox_service.delete_sandbox(sandbox_id)
            except Exception as exc:
                logger.exception(
                    'Service account sandbox delete failed',
                    extra={'user_id': str(uid), 'sandbox_id': sandbox_id},
                )
                raise LifecycleCleanupError(
                    'Sandbox cleanup incomplete; retry DELETE.'
                ) from exc
            if not deleted:
                # The row is not visible to the sandbox service (e.g. it
                # belongs to another backend). Keep going: the cleanup check
                # in ``_operate`` still blocks the delete while it remains.
                logger.warning(
                    'Service account sandbox not deleted: not visible',
                    extra={'user_id': str(uid), 'sandbox_id': sandbox_id},
                )

    async def _operate(
        self, user_id: str, operation: str, service_account: bool = False
    ):
        uid = UUID(user_id)
        # Serialize remote and local phases across replicas without holding user
        # row locks across the independently committed local revocation phase.
        async with self.session_factory() as lock_session:
            await lock_session.execute(
                text('SELECT pg_advisory_xact_lock(:namespace, hashtext(:uid))'),
                {'uid': str(uid), 'namespace': USER_LIFECYCLE_LOCK_NAMESPACE + 1},
            )
            async with self.session_factory() as session:
                await session.execute(
                    text('SELECT pg_advisory_xact_lock(:namespace, 0)'),
                    {'namespace': USER_LIFECYCLE_LOCK_NAMESPACE},
                )
                user = await session.get(User, uid, with_for_update=True)
                if user is None:
                    await session.commit()
                    if operation == 'delete':
                        try:
                            await OrgStore.delete_org_cascade(uid)
                        except Exception as exc:
                            raise LifecycleCleanupError(
                                'Personal workspace cleanup incomplete; retry DELETE.'
                            ) from exc
                    return None
                if (
                    operation == 'delete'
                    and user.account_type == ACCOUNT_TYPE_SERVICE
                    and not service_account
                ):
                    # Service accounts are removed through their org's
                    # service-account routes (``delete_service_account``).
                    # Disable/enable stay allowed.
                    raise ServiceAccountDeleteError('Cannot delete a service account')
                if service_account and user.account_type != ACCOUNT_TYPE_SERVICE:
                    raise ValueError('User is not a service account')
                result = UserLifecycleResult(str(uid), user.email)
                if operation == 'enable':
                    if user.deletion_pending:
                        raise UserCleanupConflict(
                            'Deletion is pending; retry DELETE, not enable'
                        )
                else:
                    admin_role = await session.scalar(
                        select(Role.id).where(Role.name == 'admin')
                    )
                    if (
                        admin_role is not None
                        and user.role_id == admin_role
                        and not user.is_disabled
                    ):
                        active = (
                            await session.scalars(
                                select(User.id).where(
                                    User.role_id == admin_role,
                                    User.is_disabled.is_(False),
                                )
                            )
                        ).all()
                        if len(active) <= 1:
                            raise LastSuperAdminError(
                                'Cannot disable or delete the last active superadmin'
                            )
                    if operation == 'delete':
                        await check_user_data_cleanup(session, uid)
                    user.is_disabled = True
                    user.deletion_pending = (
                        user.deletion_pending or operation == 'delete'
                    )
                    user.credentials_revoked_at = datetime.now(timezone.utc)
                    for table, column in (
                        ('api_keys', 'user_id'),
                        ('offline_tokens', 'user_id'),
                        ('auth_tokens', 'keycloak_user_id'),
                        ('device_codes', 'keycloak_user_id'),
                        ('oauth_tokens', 'user_id'),
                    ):
                        await session.execute(
                            text(f'DELETE FROM {table} WHERE {column} = :uid'),
                            {'uid': str(uid)},
                        )
                await session.commit()
            try:
                if operation == 'enable':
                    await self.remote.enable(str(uid))
                else:
                    await self.remote.disable(str(uid))
                    if operation == 'delete':
                        await self.remote.delete(str(uid))
                async with self.session_factory() as session:
                    if operation == 'enable':
                        user = await session.get(User, uid, with_for_update=True)
                        user.is_disabled = False
                    elif operation == 'delete':
                        await delete_user_data(session, uid)
                    await session.commit()
                if operation == 'delete':
                    await OrgStore.delete_org_cascade(uid)
            except UserCleanupConflict:
                raise
            except Exception as exc:
                logger.exception(
                    'User lifecycle cleanup failed',
                    extra={'user_id': user_id, 'operation': operation},
                )
                raise LifecycleCleanupError(
                    'Lifecycle cleanup incomplete; user remains disabled. Retry the same operation.'
                ) from exc
            return result
