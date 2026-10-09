import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from server.services.admin_user_lifecycle_service import (
    AdminUserLifecycleService,
    LastSuperAdminError,
    LifecycleCleanupError,
)
from storage.role import Role
from storage.user import User
from storage.user_data_cleanup import UserCleanupConflict


@pytest.fixture
def service(async_session_maker):
    # Only the remote identity/LLM boundary is replaced; DB operations are real.
    return AdminUserLifecycleService(
        session_factory=async_session_maker, remote=AsyncMock()
    )


@pytest.mark.asyncio
async def test_disable_enable_revokes_credentials(
    create_user, service, async_session_maker
):
    user = create_user()
    await service.disable_user(str(user.id))
    async with async_session_maker() as session:
        disabled = await session.get(User, user.id)
        assert disabled.is_disabled
        revoked_at = disabled.credentials_revoked_at
        assert revoked_at is not None
    await service.enable_user(str(user.id))
    async with async_session_maker() as session:
        enabled = await session.get(User, user.id)
        assert not enabled.is_disabled
        assert enabled.credentials_revoked_at == revoked_at


@pytest.mark.asyncio
async def test_remote_failure_is_disabled_and_retryable(
    create_user, service, async_session_maker
):
    user = create_user()
    service.remote.disable.side_effect = RuntimeError('external failure')
    with pytest.raises(LifecycleCleanupError):
        await service.delete_user(str(user.id))
    async with async_session_maker() as session:
        stored = await session.get(User, user.id)
        assert stored.is_disabled and stored.deletion_pending
    with pytest.raises(UserCleanupConflict):
        await service.enable_user(str(user.id))
    service.remote.disable.side_effect = None
    await service.delete_user(str(user.id))
    async with async_session_maker() as session:
        assert await session.get(User, user.id) is None


@pytest.mark.asyncio
async def test_concurrent_last_admin(create_user, service, async_session_maker):
    async with async_session_maker() as session:
        role = Role(name='admin', rank=2)
        session.add(role)
        await session.commit()
        role_id = role.id
    users = [create_user(role_id=role_id) for _ in range(2)]
    results = await asyncio.gather(
        *(service.disable_user(str(user.id)) for user in users), return_exceptions=True
    )
    assert sum(isinstance(result, LastSuperAdminError) for result in results) == 1
    async with async_session_maker() as session:
        remaining = (
            await session.scalars(select(User).where(User.is_disabled.is_(False)))
        ).all()
        assert len(remaining) == 1


@pytest.mark.asyncio
async def test_delete_preserves_shared_org_and_secrets(
    create_org, create_user, service, async_session_maker
):
    from sqlalchemy import text

    from storage.org import Org

    org = create_org()
    target = create_user(current_org_id=org.id)
    other = create_user(current_org_id=org.id)
    async with async_session_maker() as session:
        await session.execute(
            text(
                "INSERT INTO custom_secrets (keycloak_user_id, org_id, secret_name, secret_value, is_org_shared) VALUES (:uid, :org, 'shared', 'encrypted', true), (:uid, :org, 'personal', 'encrypted', false)"
            ),
            {'uid': str(target.id), 'org': org.id},
        )
        await session.execute(
            text(
                "INSERT INTO offline_tokens (user_id, offline_token) VALUES (:uid, 'old-token')"
            ),
            {'uid': str(target.id)},
        )
        await session.commit()
    await service.delete_user(str(target.id))
    async with async_session_maker() as session:
        assert await session.get(Org, org.id)
        assert await session.get(User, other.id)
        assert not await session.get(User, target.id)
        assert (
            await session.execute(
                text('SELECT secret_name, keycloak_user_id FROM custom_secrets')
            )
        ).all() == [('shared', None)]
        assert await session.scalar(text('SELECT count(*) FROM offline_tokens')) == 0
    assert await service.delete_user(str(target.id)) is None


@pytest.mark.asyncio
async def test_delete_requires_shared_workspace_transfer(
    create_user, service, async_session_maker
):
    from sqlalchemy import text

    user = create_user()
    async with async_session_maker() as session:
        await session.execute(
            text(
                'INSERT INTO jira_workspaces '
                '(name, jira_cloud_id, admin_user_id, webhook_secret, '
                'svc_acc_email, svc_acc_api_key, status) '
                "VALUES ('shared', 'cloud', :uid, 'secret', "
                "'svc@example.com', 'key', 'active')"
            ),
            {'uid': str(user.id)},
        )
        await session.commit()

    with pytest.raises(
        UserCleanupConflict,
        match='Transfer integration workspace administration',
    ):
        await service.delete_user(str(user.id))

    service.remote.disable.assert_not_awaited()
    async with async_session_maker() as session:
        stored = await session.get(User, user.id)
        assert not stored.is_disabled and not stored.deletion_pending
        assert stored.credentials_revoked_at is None
        await session.execute(
            text('DELETE FROM jira_workspaces WHERE admin_user_id = :uid'),
            {'uid': str(user.id)},
        )
        await session.commit()

    await service.delete_user(str(user.id))


@pytest.mark.asyncio
async def test_failed_enable_stays_disabled(create_user, service, async_session_maker):
    user = create_user()
    await service.disable_user(str(user.id))
    service.remote.enable.side_effect = RuntimeError('unavailable')
    with pytest.raises(LifecycleCleanupError):
        await service.enable_user(str(user.id))
    async with async_session_maker() as session:
        assert (await session.get(User, user.id)).is_disabled


def test_only_superadmin_has_lifecycle_permission():
    from server.auth.authorization import (
        ROLE_PERMISSIONS,
        SUPER_ROLE_PERMISSIONS,
        Permission,
        RoleName,
    )

    assert Permission.MANAGE_SUPER_ADMINS in SUPER_ROLE_PERMISSIONS[RoleName.ADMIN]
    assert all(
        Permission.MANAGE_SUPER_ADMINS not in permissions
        for permissions in ROLE_PERMISSIONS.values()
    )


@pytest.mark.asyncio
async def test_role_revoke_and_disable_share_last_admin_lock(
    create_user, service, async_session_maker, monkeypatch
):
    from storage.user_store import SuperAdminRevokeResult, UserStore

    monkeypatch.setattr('storage.user_store.a_session_maker', async_session_maker)
    async with async_session_maker() as session:
        role = Role(name='admin', rank=2)
        session.add(role)
        await session.commit()
    first = create_user(role_id=role.id)
    second = create_user(role_id=role.id)
    results = await asyncio.gather(
        service.disable_user(str(first.id)),
        UserStore.revoke_super_admin(str(second.id)),
        return_exceptions=True,
    )
    assert any(
        isinstance(result, LastSuperAdminError)
        or result == SuperAdminRevokeResult.LAST_SUPER_ADMIN
        for result in results
    )
    async with async_session_maker() as session:
        assert await session.scalar(
            select(User.id).where(User.role_id == role.id, User.is_disabled.is_(False))
        )


@pytest.mark.asyncio
async def test_delete_preserves_team_history(
    create_org, create_user, service, async_session_maker
):
    from sqlalchemy import text

    from storage.org_member import OrgMember
    from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas

    org = create_org()
    target = create_user(current_org_id=org.id)
    other = create_user(current_org_id=org.id)
    async with async_session_maker() as session:
        role = Role(name='member', rank=1)
        session.add(role)
        await session.flush()
        session.add(
            OrgMember(
                org_id=org.id, user_id=other.id, role_id=role.id, llm_api_key='key'
            )
        )
        session.add(
            StoredConversationMetadataSaas(
                conversation_id='retained', user_id=target.id, org_id=org.id
            )
        )
        await session.commit()
    await service.delete_user(str(target.id))
    async with async_session_maker() as session:
        conversation = await session.get(StoredConversationMetadataSaas, 'retained')
        assert conversation.user_id == other.id
        assert conversation.org_id == org.id
        assert (
            await session.scalar(
                text('SELECT count(*) FROM conversation_metadata_saas')
            )
            == 1
        )


@pytest.mark.asyncio
async def test_personal_workspace_blocks_before_mutation(
    create_org, create_user, service, async_session_maker
):
    org = create_org()
    target = create_user(id=org.id, current_org_id=org.id)
    with pytest.raises(UserCleanupConflict, match='personal workspace'):
        await service.delete_user(str(target.id))
    async with async_session_maker() as session:
        user = await session.get(User, target.id)
        assert not user.is_disabled and not user.deletion_pending
    service.remote.disable.assert_not_awaited()
