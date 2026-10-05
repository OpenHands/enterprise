"""Retryable instance-level user lifecycle operations."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text

from server.services.user_lifecycle_remote import UserLifecycleRemote
from storage.database import a_session_maker
from storage.role import Role
from storage.user import User
from storage.user_data_cleanup import check_user_data_cleanup, delete_user_data


class LastSuperAdminError(RuntimeError):
    pass


class LifecycleCleanupError(RuntimeError):
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

    async def _operate(self, user_id: str, operation: str):
        uid = UUID(user_id)
        # Serialize remote and local phases across replicas without holding user
        # row locks across the independently committed local revocation phase.
        async with self.session_factory() as lock_session:
            await lock_session.execute(
                text('SELECT pg_advisory_xact_lock(176, hashtext(:uid))'),
                {'uid': str(uid)},
            )
            async with self.session_factory() as session:
                await session.execute(text('SELECT pg_advisory_xact_lock(176, 0)'))
                user = await session.get(User, uid, with_for_update=True)
                if user is None:
                    return None
                result = UserLifecycleResult(str(uid), user.email)
                if operation == 'enable':
                    if user.deletion_pending:
                        raise LifecycleCleanupError(
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
                if operation == 'delete':
                    async with self.session_factory() as session:
                        await check_user_data_cleanup(session, uid)
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
            except ValueError as exc:
                raise LifecycleCleanupError(str(exc)) from exc
            except Exception as exc:
                raise LifecycleCleanupError(
                    'Lifecycle cleanup incomplete; user remains disabled. Retry the same operation.'
                ) from exc
            return result
