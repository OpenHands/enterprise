import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from openhands.app_server.sandbox.remote_sandbox_service import RemoteSandboxService
from openhands.app_server.sandbox.sandbox_store import REMOTE_BACKEND, StoredSandbox
from openhands.app_server.user.specifiy_user_context import USER_CONTEXT_ATTR
from server.services.admin_user_lifecycle_service import (
    AdminUserLifecycleService,
    LastSuperAdminError,
    LifecycleCleanupError,
    ServiceAccountDeleteError,
)
from storage.role import Role
from storage.user import User
from storage.user_data_cleanup import UserCleanupConflict


@pytest.fixture
def service(async_session_maker, monkeypatch):
    monkeypatch.setattr('storage.org_store.a_session_maker', async_session_maker)
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
        role = Role(name='owner', rank=1)
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
async def test_personal_workspace_deleted_with_user(
    create_org, create_user, service, async_session_maker, monkeypatch
):
    from storage.org import Org

    # The real cascade uses the same test database; only external team cleanup is replaced.
    monkeypatch.setattr('storage.org_store.a_session_maker', async_session_maker)
    monkeypatch.setattr('storage.org_store.LiteLlmManager.delete_team', AsyncMock())
    org = create_org()
    target = create_user(id=org.id, current_org_id=org.id)
    await service.delete_user(str(target.id))
    async with async_session_maker() as session:
        assert await session.get(User, target.id) is None
        assert await session.get(Org, org.id) is None


@pytest.mark.asyncio
async def test_last_team_owner_blocks_before_mutation(
    create_org, create_user, service, async_session_maker
):
    from storage.org_member import OrgMember

    org = create_org()
    target = create_user(current_org_id=org.id)
    create_user(current_org_id=org.id)
    async with async_session_maker() as session:
        role = Role(name='owner', rank=1)
        session.add(role)
        await session.flush()
        session.add(
            OrgMember(
                org_id=org.id, user_id=target.id, role_id=role.id, llm_api_key='key'
            )
        )
        await session.commit()
    with pytest.raises(UserCleanupConflict, match='last owner'):
        await service.delete_user(str(target.id))
    service.remote.disable.assert_not_awaited()
    async with async_session_maker() as session:
        assert not (await session.get(User, target.id)).is_disabled


@pytest.mark.asyncio
async def test_personal_workspace_cleanup_retries_after_user_is_gone(
    create_org, create_user, service, async_session_maker, monkeypatch
):
    from storage.org import Org

    org = create_org()
    target = create_user(id=org.id, current_org_id=org.id)
    delete_team = AsyncMock(side_effect=RuntimeError('gateway unavailable'))
    monkeypatch.setattr('storage.org_store.LiteLlmManager.delete_team', delete_team)
    with pytest.raises(LifecycleCleanupError):
        await service.delete_user(str(target.id))
    async with async_session_maker() as session:
        assert await session.get(User, target.id) is None
        assert await session.get(Org, org.id) is not None
    delete_team.side_effect = None
    assert await service.delete_user(str(target.id)) is None
    async with async_session_maker() as session:
        assert await session.get(Org, org.id) is None


@pytest.mark.asyncio
async def test_team_conversation_requires_remaining_owner(
    create_org, create_user, service, async_session_maker
):
    from storage.org_member import OrgMember
    from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas

    org = create_org()
    target = create_user(current_org_id=org.id)
    member = create_user(current_org_id=org.id)
    async with async_session_maker() as session:
        role = Role(name='member', rank=1)
        session.add(role)
        await session.flush()
        session.add(
            OrgMember(
                org_id=org.id, user_id=member.id, role_id=role.id, llm_api_key='key'
            )
        )
        session.add(
            StoredConversationMetadataSaas(
                conversation_id='private', user_id=target.id, org_id=org.id
            )
        )
        await session.commit()
    with pytest.raises(UserCleanupConflict, match='Transfer conversation ownership'):
        await service.delete_user(str(target.id))
    service.remote.disable.assert_not_awaited()
    async with async_session_maker() as session:
        assert (
            await session.get(StoredConversationMetadataSaas, 'private')
        ).user_id == target.id
        assert not (await session.get(User, target.id)).is_disabled


@pytest.mark.asyncio
async def test_delete_refuses_service_account_but_disable_enable_work(
    create_user, service, async_session_maker
):
    user = create_user(account_type='service')
    with pytest.raises(ServiceAccountDeleteError):
        await service.delete_user(str(user.id))
    async with async_session_maker() as session:
        stored = await session.get(User, user.id)
        assert not stored.is_disabled and not stored.deletion_pending
    # Disable and enable stay allowed: they are the kill switch.
    await service.disable_user(str(user.id))
    await service.enable_user(str(user.id))
    # A service account has no Keycloak identity to update.
    service.remote.disable.assert_awaited_once_with(str(user.id), idp_user=False)
    service.remote.enable.assert_awaited_once_with(str(user.id), idp_user=False)


@pytest.mark.asyncio
async def test_human_lifecycle_updates_the_idp_identity(create_user, service):
    user = create_user()
    await service.disable_user(str(user.id))
    await service.enable_user(str(user.id))
    service.remote.disable.assert_awaited_once_with(str(user.id), idp_user=True)
    service.remote.enable.assert_awaited_once_with(str(user.id), idp_user=True)


@pytest.mark.asyncio
async def test_delete_service_account_removes_it_and_refuses_humans(
    create_user, service, async_session_maker
):
    """GIVEN a service account and a human WHEN deleted through the explicit path THEN the service account is removed and the human is refused."""
    sa = create_user(account_type='service')
    human = create_user()

    await service.delete_service_account(str(sa.id))

    async with async_session_maker() as session:
        assert await session.get(User, sa.id) is None
    service.remote.disable.assert_awaited_once_with(str(sa.id), idp_user=False)
    service.remote.delete.assert_awaited_once_with(str(sa.id), idp_user=False)
    with pytest.raises(ValueError):
        await service.delete_service_account(str(human.id))
    async with async_session_maker() as session:
        assert await session.get(User, human.id) is not None


# --- Service account sandboxes ---


@pytest.fixture
def sandbox_service(async_session_maker):
    """A real ``RemoteSandboxService`` on the test DB, with the runtime API faked.

    The runtime answers 404, as for a paused sandbox, so ``delete_sandbox``
    deletes the row. ``get_sandbox_service`` is patched where the lifecycle
    service imports it; the states it was called with are recorded.
    """
    states = []

    @asynccontextmanager
    async def get_sandbox_service(state, request=None):
        states.append(state)
        runtime_gone = httpx.Response(
            404, request=httpx.Request('GET', 'https://runtime/sessions/x')
        )
        client = AsyncMock(spec=httpx.AsyncClient)
        client.request.return_value = runtime_gone
        async with async_session_maker() as session:
            service = RemoteSandboxService(
                sandbox_spec_service=AsyncMock(),
                api_url='https://runtime',
                api_key='k',
                web_url=None,
                resource_factor=1,
                runtime_class='gvisor',
                start_sandbox_timeout=1,
                max_num_sandboxes=1,
                user_context=getattr(state, USER_CONTEXT_ATTR),
                httpx_client=client,
                db_session=session,
            )
            yield service
            await session.commit()

    with patch(
        'openhands.app_server.config.get_sandbox_service', get_sandbox_service
    ) as patched:
        patched.states = states
        yield patched


async def _add_sandbox(async_session_maker, sandbox_id, user_id, paused=True):
    async with async_session_maker() as session:
        session.add(
            StoredSandbox(
                id=sandbox_id,
                backend=REMOTE_BACKEND,
                created_by_user_id=str(user_id),
                sandbox_spec_id='spec',
                lifecycle_state='paused' if paused else 'running',
            )
        )
        await session.commit()


async def _sandbox_ids(async_session_maker):
    async with async_session_maker() as session:
        return set((await session.scalars(select(StoredSandbox.id))).all())


@pytest.mark.asyncio
async def test_delete_service_account_deletes_its_paused_sandboxes(
    create_user, service, async_session_maker, sandbox_service
):
    """GIVEN a service account with paused sandboxes WHEN deleted THEN its sandboxes and the account are gone and other users' sandboxes stay."""
    sa = create_user(account_type='service')
    other = create_user()
    await _add_sandbox(async_session_maker, 'sa-1', sa.id)
    await _add_sandbox(async_session_maker, 'sa-2', sa.id, paused=False)
    await _add_sandbox(async_session_maker, 'other', other.id)

    await service.delete_service_account(str(sa.id))

    assert await _sandbox_ids(async_session_maker) == {'other'}
    async with async_session_maker() as session:
        assert await session.get(User, sa.id) is None
    # The sandbox service acted for the service account only.
    assert {
        getattr(state, USER_CONTEXT_ATTR).user_id for state in sandbox_service.states
    } == {str(sa.id)}


@pytest.mark.asyncio
async def test_delete_service_account_keeps_the_account_when_a_sandbox_fails(
    create_user, service, async_session_maker, sandbox_service
):
    sa = create_user(account_type='service')
    await _add_sandbox(async_session_maker, 'sa-1', sa.id)

    with patch.object(
        RemoteSandboxService, 'delete_sandbox', side_effect=RuntimeError('stop failed')
    ):
        with pytest.raises(LifecycleCleanupError):
            await service.delete_service_account(str(sa.id))

    assert await _sandbox_ids(async_session_maker) == {'sa-1'}
    async with async_session_maker() as session:
        assert await session.get(User, sa.id) is not None


@pytest.mark.asyncio
async def test_delete_user_still_blocked_by_sandboxes_and_deletes_none(
    create_user, service, async_session_maker, sandbox_service
):
    """Human behavior is unchanged: sandboxes block the delete and are not touched."""
    human = create_user()
    await _add_sandbox(async_session_maker, 'h-1', human.id)

    with pytest.raises(UserCleanupConflict):
        await service.delete_user(str(human.id))

    assert await _sandbox_ids(async_session_maker) == {'h-1'}
    assert sandbox_service.states == []


@pytest.mark.asyncio
async def test_delete_service_account_warns_when_a_sandbox_is_not_visible(
    create_user, service, async_session_maker, sandbox_service
):
    """GIVEN a sandbox row the sandbox service cannot see WHEN deleted THEN a warning is logged and the cleanup check still blocks the delete."""
    sa = create_user(account_type='service')
    await _add_sandbox(async_session_maker, 'sa-1', sa.id)

    with (
        patch.object(
            RemoteSandboxService, 'delete_sandbox', AsyncMock(return_value=False)
        ),
        patch('server.services.admin_user_lifecycle_service.logger') as log,
    ):
        with pytest.raises(UserCleanupConflict):
            await service.delete_service_account(str(sa.id))

    log.warning.assert_called_once()
    assert log.warning.call_args.kwargs['extra'] == {
        'user_id': str(sa.id),
        'sandbox_id': 'sa-1',
    }
    assert await _sandbox_ids(async_session_maker) == {'sa-1'}
    async with async_session_maker() as session:
        assert await session.get(User, sa.id) is not None
