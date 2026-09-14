"""Enrollment links preserve account and organization authority."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from server.auth import auth_config
from server.auth.native_password import NativeAuthError
from server.auth.native_session import digest_token
from server.auth.native_types import SessionFactory
from server.services.native_account_admin_service import NativeAccountAdminService
from server.services.native_account_service import set_superadmin, tombstone_account
from storage.account_invitation import AccountInvitation
from storage.native_auth import PasswordCredential
from storage.org import Org
from storage.org_member import OrgMember
from storage.role import Role
from storage.user import User
from tests.unit.server.auth.enrollment_fixtures import (
    NEW_PASSWORD,
    PASSWORD,
    NativeFixture,
    enroll,
    link_token,
    present,
)

pytest_plugins = ['tests.unit.server.auth.enrollment_fixtures']


@pytest.mark.parametrize('web_path', ['', '/openhands/'])
async def test_enrollment_is_single_use_and_does_not_grant_admin(
    native: NativeFixture,
    configured: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    web_path: str,
) -> None:
    service, admin_id = native
    web_url = f'https://public.example.test:8443{web_path}'
    monkeypatch.setenv('OH_WEB_URL', web_url)
    auth_config.get_native_auth_settings.cache_clear()
    link = await service.issue_invitation(admin_id, 'Invited@Example.test')
    assert link['invite_url'].startswith(f'{web_url.rstrip("/")}/account-setup#token=')
    token = link_token(link)
    assert (await service.inspect_invitation(token))['action'] == 'set_password'
    result = await service.complete_enrollment(token, PASSWORD, client_ip='1')
    async with configured() as session:
        user = await session.get(User, present(result).principal.account_id)
        invitation = await session.get(AccountInvitation, UUID(link['invitation_id']))
        assert present(user).role_id is None
        assert present(invitation).accepted_account_id == present(user).id
        assert present(invitation).token_digest == digest_token(token, 'enrollment')
    with pytest.raises(NativeAuthError, match='Invalid or expired'):
        await service.complete_enrollment(token, NEW_PASSWORD, client_ip='1')


async def test_competing_links_resolve_durable_account_without_password_overwrite(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    first = await service.issue_invitation(admin_id, 'same@example.test')
    second = await service.issue_invitation(admin_id, 'SAME@example.test')
    logged_in = await service.complete_enrollment(
        link_token(first), PASSWORD, client_ip='1'
    )
    assert (await service.inspect_invitation(link_token(second)))['action'] == 'login'
    assert (
        await service.complete_enrollment(
            link_token(second), NEW_PASSWORD, client_ip='1'
        )
        is None
    )
    with pytest.raises(NativeAuthError, match='Sign in as the invited account'):
        await service.accept_membership(admin_id, link_token(second))
    await service.accept_membership(
        present(logged_in).principal.account_id, link_token(second)
    )
    again = await service.auth.login('same@example.test', PASSWORD, client_ip='1')
    assert (
        present(again).principal.account_id == present(logged_in).principal.account_id
    )
    async with configured() as session:
        invitation = await session.get(AccountInvitation, UUID(second['invitation_id']))
        assert (
            present(invitation).reserved_account_id
            != present(invitation).accepted_account_id
        )
        assert present(invitation).accepted_account_id == again.principal.account_id


async def test_creator_demotion_invalidates_invitation(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    other = await enroll(service, admin_id)
    async with configured() as session, session.begin():
        await set_superadmin(session, other.principal.account_id, True)
    link = await service.issue_invitation(admin_id, 'another@example.test')
    async with configured() as session, session.begin():
        await set_superadmin(session, admin_id, False)
    with pytest.raises(NativeAuthError, match='Global user management'):
        await service.inspect_invitation(link_token(link))


async def test_reissue_invalidates_old_link_and_lists_never_expose_secret(
    native: NativeFixture,
) -> None:
    service, admin_id = native
    first = await service.issue_invitation(admin_id, 'reissued@example.test')
    second = await service.reissue_invitation(admin_id, UUID(first['invitation_id']))
    with pytest.raises(NativeAuthError, match='Invalid or expired'):
        await service.inspect_invitation(link_token(first))
    assert (await service.inspect_invitation(link_token(second)))[
        'action'
    ] == 'set_password'
    metadata = await service.list_invitations(admin_id)
    assert len(metadata['items']) == 2
    assert 'token' not in str(metadata)
    await service.revoke_invitation(admin_id, UUID(second['invitation_id']))
    with pytest.raises(NativeAuthError, match='Invalid or expired'):
        await service.inspect_invitation(link_token(second))


async def test_scoped_invitation_rechecks_and_adds_only_authorized_membership(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    async with configured() as session:
        role_id = await session.scalar(select(Role.id).where(Role.name == 'member'))
    invitation = await service.issue_invitation(
        admin_id, 'scoped@example.test', admin_id, role_id
    )
    result = await service.complete_enrollment(
        link_token(invitation), PASSWORD, client_ip='1'
    )
    async with configured() as session:
        member = await session.get(
            OrgMember, (admin_id, present(result).principal.account_id)
        )
        assert present(member).role_id == role_id
        assert (
            present(await session.get(User, present(result).principal.account_id))
        ).role_id is None
        memberships = (
            await session.scalars(
                select(OrgMember).where(
                    OrgMember.user_id == present(result).principal.account_id
                )
            )
        ).all()
        assert len(memberships) == 2
    with pytest.raises(NativeAuthError, match='Global user management'):
        await service.issue_invitation(
            present(result).principal.account_id, 'other@example.test'
        )


async def test_concurrent_consumption_creates_one_account(
    native: NativeFixture,
    configured: SessionFactory,
) -> None:
    service, admin_id = native
    token = link_token(await service.issue_invitation(admin_id, 'race@example.test'))

    async def consume() -> bool:
        try:
            result = await service.complete_enrollment(token, PASSWORD, client_ip='1')
            return result is not None
        except NativeAuthError:
            return False

    assert sorted(await asyncio.gather(consume(), consume())) == [False, True]
    async with configured() as session:
        credentials = (
            await session.scalars(
                select(PasswordCredential).where(
                    PasswordCredential.normalized_login_email == 'race@example.test',
                )
            )
        ).all()
        assert len(credentials) == 1


async def test_terminal_deletion_revokes_all_earlier_email_links(
    native: NativeFixture,
    configured: SessionFactory,
) -> None:
    service, admin_id = native
    first = await service.issue_invitation(admin_id, 'deleted@example.test')
    pending = await service.issue_invitation(admin_id, 'deleted@example.test')
    login = present(
        await service.complete_enrollment(
            link_token(first),
            PASSWORD,
            client_ip='1',
        )
    )
    async with configured() as session, session.begin():
        await tombstone_account(session, login.principal.account_id)
    with pytest.raises(NativeAuthError, match='Invalid or expired'):
        await service.inspect_invitation(link_token(pending))
    async with configured() as session:
        row = present(
            await session.get(AccountInvitation, UUID(pending['invitation_id']))
        )
        assert row.revoked_at is not None


async def test_global_admin_seeds_team_but_cannot_invite_to_foreign_personal_org(
    native: NativeFixture,
    configured: SessionFactory,
) -> None:
    service, admin_id = native
    person = await enroll(service, admin_id)
    async with configured() as session, session.begin():
        team = Org(id=uuid4(), name='New team')
        session.add(team)
        role_id = present(
            await session.scalar(select(Role.id).where(Role.name == 'owner'))
        )
        team_id = team.id
    choices = await service.list_invitation_organizations(admin_id)
    assert choices == {'items': [{'id': str(team_id), 'name': 'New team'}], 'total': 1}
    invitation = await service.issue_invitation(
        admin_id,
        'owner@example.test',
        team_id,
        role_id,
    )
    owner = present(
        await service.complete_enrollment(
            link_token(invitation),
            PASSWORD,
            client_ip='1',
        )
    )
    async with configured() as session:
        assert await session.get(OrgMember, (team_id, admin_id)) is None
        assert (
            present(
                await session.get(OrgMember, (team_id, owner.principal.account_id))
            ).role_id
            == role_id
        )
    with pytest.raises(NativeAuthError, match='Not authorized'):
        await service.issue_invitation(
            admin_id,
            'wrong@example.test',
            person.principal.account_id,
            role_id,
        )


async def test_account_metadata_and_expired_invitation_cleanup(
    native: NativeFixture,
    configured: SessionFactory,
) -> None:
    service, admin_id = native
    person = await enroll(service, admin_id)
    pending = await service.issue_invitation(admin_id, 'person@example.test')
    admin = NativeAccountAdminService(configured)
    page = await admin.list_accounts(admin_id, account_id=person.principal.account_id)
    assert page['total'] == 1
    account = page['items'][0]
    assert account['authentication_methods'] == ['password']
    assert account['pending_invitations'][0]['id'] == pending['invitation_id']
    assert 'token' not in str(page) and 'password_hash' not in str(page)
    with pytest.raises(NativeAuthError, match='Global user management'):
        await admin.list_accounts(person.principal.account_id)
    async with configured() as session, session.begin():
        invitation = present(
            await session.get(AccountInvitation, UUID(pending['invitation_id']))
        )
        invitation.expires_at = datetime.now(UTC) - timedelta(days=8)
    await service.cleanup_expired_state()
    async with configured() as session:
        assert (
            await session.get(AccountInvitation, UUID(pending['invitation_id'])) is None
        )
