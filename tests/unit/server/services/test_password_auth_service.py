"""Tests for PasswordAuthService against a real database.

These cover the authorization rules and the token lifecycle, which decide who
can take over which account, so they exercise the service end to end rather
than asserting on mocks.
"""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from argon2 import PasswordHasher

from server.auth.password_auth import PasswordAuthError, utc_now
from server.services.password_auth_service import PasswordAuthService
from storage.org_invitation import OrgInvitation
from storage.org_member import OrgMember
from storage.password_auth import PasswordAuthAccount, PasswordAuthToken
from storage.role import Role
from storage.user import User

PASSWORD = 'correct horse battery'

# Every store the service reaches through opens its own session, so each one
# has to be pointed at the test database.
SESSION_MAKER_PATHS = (
    'server.services.password_auth_service.a_session_maker',
    'storage.user_store.a_session_maker',
    'storage.org_member_store.a_session_maker',
    'storage.role_store.a_session_maker',
    'storage.org_invitation_store.a_session_maker',
)


@pytest.fixture
def password_auth_enabled(async_session_maker, monkeypatch):
    monkeypatch.setenv('ENABLE_PASSWORD_AUTH', 'true')
    with patch('server.auth.password_auth.DEPLOYMENT_MODE', 'self_hosted'):
        with (
            patch(SESSION_MAKER_PATHS[0], async_session_maker),
            patch(SESSION_MAKER_PATHS[1], async_session_maker),
            patch(SESSION_MAKER_PATHS[2], async_session_maker),
            patch(SESSION_MAKER_PATHS[3], async_session_maker),
            patch(SESSION_MAKER_PATHS[4], async_session_maker),
        ):
            yield


@pytest.fixture
def org_fixtures(session_maker):
    """An org and the owner/admin/member role rows."""
    from storage.org import Org

    with session_maker() as session:
        org = Org(id=uuid.uuid4(), name=f'org-{uuid.uuid4().hex[:8]}')
        session.add(org)
        roles = {
            'owner': Role(name='owner', rank=10),
            'admin': Role(name='admin', rank=20),
            'member': Role(name='member', rank=1000),
        }
        session.add_all(list(roles.values()))
        session.commit()
        return {
            'org_id': org.id,
            'role_ids': {name: role.id for name, role in roles.items()},
        }


@pytest.fixture
def make_member(session_maker, org_fixtures):
    """Create a user, their org membership and their password account.

    A superadmin is the admin role row referenced from ``user.role_id``, which
    is what makes it instance-wide rather than org-scoped.
    """

    def _make(role: str, *, email: str | None = None, is_superadmin: bool = False):
        user_id = uuid.uuid4()
        address = email or f'{user_id.hex[:8]}@example.com'
        with session_maker() as session:
            session.add(
                User(
                    id=user_id,
                    current_org_id=org_fixtures['org_id'],
                    email=address,
                    role_id=(
                        org_fixtures['role_ids']['admin'] if is_superadmin else None
                    ),
                )
            )
            session.flush()
            session.add(
                OrgMember(
                    org_id=org_fixtures['org_id'],
                    user_id=user_id,
                    role_id=org_fixtures['role_ids'][role],
                    status='active',
                    llm_api_key='test-key',
                )
            )
            session.add(
                PasswordAuthAccount(
                    user_id=user_id,
                    normalized_email=address,
                    password_hash=PasswordHasher().hash(PASSWORD),
                )
            )
            session.commit()
        return user_id, address

    return _make


@pytest.fixture
def make_invitation(session_maker, org_fixtures):
    def _make(email: str, inviter_id: uuid.UUID, **kwargs) -> OrgInvitation:
        with session_maker() as session:
            invitation = OrgInvitation(
                token=uuid.uuid4().hex,
                org_id=org_fixtures['org_id'],
                email=email,
                role_id=org_fixtures['role_ids']['member'],
                inviter_id=inviter_id,
                status=kwargs.get('status', OrgInvitation.STATUS_PENDING),
                expires_at=kwargs.get(
                    'expires_at', utc_now().replace(tzinfo=None) + timedelta(days=7)
                ),
            )
            session.add(invitation)
            session.commit()
            session.refresh(invitation)
            session.expunge(invitation)
            return invitation

    return _make


@pytest.mark.asyncio
async def test_setup_link_refused_for_an_existing_account(
    password_auth_enabled, org_fixtures, make_member, make_invitation
):
    """Inviting someone who already has an account must not hand out a link.

    Otherwise any admin could invite an existing user - an SSO user or a
    superadmin - and set their password from the returned setup link.
    """
    owner_id, _ = make_member('owner')
    victim_id, victim_email = make_member('admin', is_superadmin=True)
    invitation = make_invitation(victim_email, owner_id)

    link = await PasswordAuthService.issue_setup_link_for_invitation(
        invitation, owner_id
    )

    assert link is None


@pytest.mark.asyncio
async def test_setup_link_issued_for_a_new_invitee(
    password_auth_enabled, session_maker, make_member, make_invitation
):
    owner_id, _ = make_member('owner')
    invitation = make_invitation('newcomer@example.com', owner_id)

    link = await PasswordAuthService.issue_setup_link_for_invitation(
        invitation, owner_id
    )

    assert link is not None
    assert link.purpose == 'setup'
    with session_maker() as session:
        account = session.query(PasswordAuthAccount).filter_by(
            normalized_email='newcomer@example.com'
        )
        assert account.one().created_by_org_invitation_id == invitation.id


@pytest.mark.asyncio
async def test_second_invitation_cannot_claim_a_provisioned_account(
    password_auth_enabled, make_member, make_invitation
):
    """Only the invitation that provisioned the account may set its password."""
    owner_id, _ = make_member('owner')
    first = make_invitation('newcomer@example.com', owner_id)
    assert await PasswordAuthService.issue_setup_link_for_invitation(first, owner_id)

    second = make_invitation('newcomer@example.com', owner_id)
    assert (
        await PasswordAuthService.issue_setup_link_for_invitation(second, owner_id)
        is None
    )


@pytest.mark.asyncio
async def test_reset_refused_for_self(password_auth_enabled, org_fixtures, make_member):
    owner_id, _ = make_member('owner')

    with pytest.raises(PasswordAuthError) as exc:
        await PasswordAuthService.issue_reset_link(
            org_fixtures['org_id'], owner_id, owner_id
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_reset_an_owner(
    password_auth_enabled, org_fixtures, make_member
):
    admin_id, _ = make_member('admin')
    owner_id, _ = make_member('owner')

    with pytest.raises(PasswordAuthError) as exc:
        await PasswordAuthService.issue_reset_link(
            org_fixtures['org_id'], owner_id, admin_id
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_reset_a_superadmin(
    password_auth_enabled, org_fixtures, make_member
):
    admin_id, _ = make_member('admin')
    target_id, _ = make_member('member', is_superadmin=True)

    with pytest.raises(PasswordAuthError) as exc:
        await PasswordAuthService.issue_reset_link(
            org_fixtures['org_id'], target_id, admin_id
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_reset_a_member_who_owns_another_org(
    password_auth_enabled, session_maker, org_fixtures, make_member
):
    """A password is instance-wide, so every org the target is in counts."""
    from storage.org import Org

    admin_id, _ = make_member('admin')
    target_id, _ = make_member('member')
    with session_maker() as session:
        other_org = Org(id=uuid.uuid4(), name=f'other-{uuid.uuid4().hex[:8]}')
        session.add(other_org)
        session.add(
            OrgMember(
                org_id=other_org.id,
                user_id=target_id,
                role_id=org_fixtures['role_ids']['owner'],
                status='active',
                llm_api_key='test-key',
            )
        )
        session.commit()

    with pytest.raises(PasswordAuthError) as exc:
        await PasswordAuthService.issue_reset_link(
            org_fixtures['org_id'], target_id, admin_id
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_reset_an_admin(
    password_auth_enabled, org_fixtures, make_member
):
    owner_id, _ = make_member('owner')
    admin_id, _ = make_member('admin')

    link = await PasswordAuthService.issue_reset_link(
        org_fixtures['org_id'], admin_id, owner_id
    )

    assert link.purpose == 'reset'


@pytest.mark.asyncio
async def test_completing_a_setup_link_sets_the_password_once(
    password_auth_enabled, session_maker, make_member, make_invitation
):
    owner_id, _ = make_member('owner')
    invitation = make_invitation('newcomer@example.com', owner_id)
    link = await PasswordAuthService.issue_setup_link_for_invitation(
        invitation, owner_id
    )
    assert link is not None

    with patch.object(
        PasswordAuthService, '_accept_invitation_after_setup', new_callable=AsyncMock
    ):
        user_id, version = await PasswordAuthService.complete_token(
            link.token, PASSWORD
        )

    assert version == 2
    with session_maker() as session:
        account = session.get(PasswordAuthAccount, user_id)
        assert account.password_hash is not None

    assert (await PasswordAuthService.inspect_token(link.token)).status == 'used'
    with pytest.raises(PasswordAuthError) as exc:
        await PasswordAuthService.complete_token(link.token, PASSWORD)
    assert exc.value.code == 'used'


@pytest.mark.asyncio
async def test_revoking_the_invitation_invalidates_its_setup_link(
    password_auth_enabled, session_maker, make_member, make_invitation
):
    owner_id, _ = make_member('owner')
    invitation = make_invitation('newcomer@example.com', owner_id)
    link = await PasswordAuthService.issue_setup_link_for_invitation(
        invitation, owner_id
    )
    assert link is not None
    with session_maker() as session:
        session.query(OrgInvitation).filter_by(id=invitation.id).update(
            {'status': OrgInvitation.STATUS_REVOKED}
        )
        session.commit()

    assert (await PasswordAuthService.inspect_token(link.token)).status == 'invalid'
    with pytest.raises(PasswordAuthError) as exc:
        await PasswordAuthService.complete_token(link.token, PASSWORD)
    assert exc.value.code == 'invalid'


@pytest.mark.asyncio
async def test_expired_link_is_reported_as_expired(
    password_auth_enabled, session_maker, org_fixtures, make_member
):
    owner_id, _ = make_member('owner')
    admin_id, _ = make_member('admin')
    link = await PasswordAuthService.issue_reset_link(
        org_fixtures['org_id'], admin_id, owner_id
    )
    with session_maker() as session:
        session.query(PasswordAuthToken).filter_by(user_id=admin_id).update(
            {'expires_at': utc_now() - timedelta(minutes=1)}
        )
        session.commit()

    assert (await PasswordAuthService.inspect_token(link.token)).status == 'expired'
    with pytest.raises(PasswordAuthError) as exc:
        await PasswordAuthService.complete_token(link.token, PASSWORD)
    assert exc.value.code == 'expired'


@pytest.mark.asyncio
async def test_issuing_a_link_revokes_the_previous_one(
    password_auth_enabled, org_fixtures, make_member
):
    owner_id, _ = make_member('owner')
    admin_id, _ = make_member('admin')

    first = await PasswordAuthService.issue_reset_link(
        org_fixtures['org_id'], admin_id, owner_id
    )
    second = await PasswordAuthService.issue_reset_link(
        org_fixtures['org_id'], admin_id, owner_id
    )

    assert (await PasswordAuthService.inspect_token(first.token)).status == 'invalid'
    assert (await PasswordAuthService.inspect_token(second.token)).status == 'valid'


@pytest.mark.asyncio
async def test_reset_invalidates_existing_sessions(
    password_auth_enabled, org_fixtures, make_member
):
    owner_id, _ = make_member('owner')
    admin_id, _ = make_member('admin')
    assert await PasswordAuthService.validate_session(admin_id, 1) is True
    link = await PasswordAuthService.issue_reset_link(
        org_fixtures['org_id'], admin_id, owner_id
    )

    _, version = await PasswordAuthService.complete_token(link.token, PASSWORD)

    assert version == 2
    assert await PasswordAuthService.validate_session(admin_id, 1) is False
    assert await PasswordAuthService.validate_session(admin_id, 2) is True


@pytest.mark.asyncio
async def test_login_accepts_the_right_password(password_auth_enabled, make_member):
    user_id, email = make_member('member')

    assert await PasswordAuthService.login(email, PASSWORD) == (user_id, 1)


@pytest.mark.asyncio
async def test_unknown_email_and_wrong_password_both_fail_alike(
    password_auth_enabled, make_member
):
    _, email = make_member('member')

    for address in (email, 'nobody@example.com', 'not-an-email'):
        with pytest.raises(PasswordAuthError) as exc:
            await PasswordAuthService.login(address, 'not-the-password')
        assert exc.value.status_code == 401
        assert exc.value.code == 'invalid_credentials'


@pytest.mark.asyncio
async def test_unreadable_stored_hash_denies_login(
    password_auth_enabled, session_maker, make_member
):
    user_id, email = make_member('member')
    with session_maker() as session:
        session.query(PasswordAuthAccount).filter_by(user_id=user_id).update(
            {'password_hash': 'not-an-argon2-hash'}
        )
        session.commit()

    with pytest.raises(PasswordAuthError) as exc:
        await PasswordAuthService.login(email, PASSWORD)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_revoked_invitation_discards_its_unclaimed_account(
    password_auth_enabled, session_maker, make_member, make_invitation
):
    owner_id, _ = make_member('owner')
    invitation = make_invitation('newcomer@example.com', owner_id)
    await PasswordAuthService.issue_setup_link_for_invitation(invitation, owner_id)

    await PasswordAuthService.discard_unclaimed_invitation_account(invitation.id)

    with session_maker() as session:
        assert (
            session.query(PasswordAuthAccount)
            .filter_by(normalized_email='newcomer@example.com')
            .one_or_none()
            is None
        )


@pytest.mark.asyncio
async def test_discard_keeps_an_account_that_set_a_password(
    password_auth_enabled, session_maker, make_member, make_invitation
):
    owner_id, _ = make_member('owner')
    invitation = make_invitation('newcomer@example.com', owner_id)
    link = await PasswordAuthService.issue_setup_link_for_invitation(
        invitation, owner_id
    )
    assert link is not None
    with patch.object(
        PasswordAuthService, '_accept_invitation_after_setup', new_callable=AsyncMock
    ):
        await PasswordAuthService.complete_token(link.token, PASSWORD)

    await PasswordAuthService.discard_unclaimed_invitation_account(invitation.id)

    with session_maker() as session:
        assert (
            session.query(PasswordAuthAccount)
            .filter_by(normalized_email='newcomer@example.com')
            .one_or_none()
            is not None
        )
