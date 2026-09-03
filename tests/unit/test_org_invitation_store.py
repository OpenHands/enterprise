"""Tests for organization invitation store.

The DB-touching tests use the shared SQLite ``async_session_maker`` fixture
(see ``tests/unit/conftest.py``) rather than mocking the session.
This exercises real SQL semantics: column constraints, unique indexes,
``joinedload`` relationships, and the commit/re-fetch round-trip that the
store performs. Pure-logic helpers (token generation, expiry branching) keep
using lightweight mocks since no SQL is involved.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from storage.org import Org
from storage.org_invitation import OrgInvitation
from storage.org_invitation_store import (
    INVITATION_TOKEN_LENGTH,
    INVITATION_TOKEN_PREFIX,
    OrgInvitationStore,
)
from storage.role import Role
from storage.user import User


@pytest.fixture
async def seeded_org_role_user(async_session_maker):
    """Seed the minimal rows required by OrgInvitation's foreign keys.

    Returns a tuple ``(org_id, role_id, user_id)``.
    """
    org = Org(name=f'test-org-{uuid4()}')
    role = Role(name=f'role-{uuid4()}', rank=1)
    async with async_session_maker() as session:
        session.add(org)
        session.add(role)
        await session.flush()
        user = User(id=uuid4(), current_org_id=org.id)
        session.add(user)
        await session.commit()
        return org.id, role.id, user.id


@pytest.fixture
def store_with_session(async_session_maker):
    """Patch ``a_session_maker`` in the store module to use the SQLite fixture."""
    with patch('storage.org_invitation_store.a_session_maker', async_session_maker):
        yield


class TestGenerateToken:
    """Test cases for token generation."""

    def test_generate_token_has_correct_prefix(self):
        """Test that generated tokens have the correct prefix."""
        token = OrgInvitationStore.generate_token()
        assert token.startswith(INVITATION_TOKEN_PREFIX)

    def test_generate_token_has_correct_length(self):
        """Test that generated tokens have the correct total length."""
        token = OrgInvitationStore.generate_token()
        expected_length = len(INVITATION_TOKEN_PREFIX) + INVITATION_TOKEN_LENGTH
        assert len(token) == expected_length

    def test_generate_token_uses_alphanumeric_characters(self):
        """Test that generated tokens use only alphanumeric characters."""
        token = OrgInvitationStore.generate_token()
        # Remove prefix and check the rest is alphanumeric
        random_part = token[len(INVITATION_TOKEN_PREFIX) :]
        assert random_part.isalnum()

    def test_generate_token_is_unique(self):
        """Test that generated tokens are unique (probabilistically)."""
        tokens = [OrgInvitationStore.generate_token() for _ in range(100)]
        assert len(set(tokens)) == 100


class TestIsTokenExpired:
    """Test cases for token expiration checking."""

    def test_token_not_expired_when_future(self):
        """Test that tokens with future expiration are not expired."""
        invitation = MagicMock(spec=OrgInvitation)
        invitation.expires_at = datetime.utcnow() + timedelta(days=1)

        result = OrgInvitationStore.is_token_expired(invitation)
        assert result is False

    def test_token_expired_when_past(self):
        """Test that tokens with past expiration are expired."""
        invitation = MagicMock(spec=OrgInvitation)
        invitation.expires_at = datetime.utcnow() - timedelta(seconds=1)

        result = OrgInvitationStore.is_token_expired(invitation)
        assert result is True

    def test_token_expired_at_exact_boundary(self):
        """Test that tokens at exact expiration time are expired."""
        # A token that expires "now" should be expired
        now = datetime.utcnow()
        invitation = MagicMock(spec=OrgInvitation)
        invitation.expires_at = now - timedelta(microseconds=1)

        result = OrgInvitationStore.is_token_expired(invitation)
        assert result is True


class TestCreateInvitation:
    """Test cases for invitation creation."""

    @pytest.mark.asyncio
    async def test_create_invitation_normalizes_email(
        self, async_session_maker, seeded_org_role_user, store_with_session
    ):
        """Email is normalized (lowercase, stripped) on creation and persisted."""
        org_id, role_id, inviter_id = seeded_org_role_user

        invitation = await OrgInvitationStore.create_invitation(
            org_id=org_id,
            email='  TEST@EXAMPLE.COM  ',
            role_id=role_id,
            inviter_id=inviter_id,
        )

        # The returned (re-fetched) row carries the normalized email.
        assert invitation is not None
        assert invitation.email == 'test@example.com'

        # Verify the row actually landed in the DB with the normalized value.
        async with async_session_maker() as session:
            row = await session.get(OrgInvitation, invitation.id)
            assert row is not None
            assert row.email == 'test@example.com'
            assert row.status == OrgInvitation.STATUS_PENDING
            assert row.token.startswith(INVITATION_TOKEN_PREFIX)

    @pytest.mark.asyncio
    async def test_create_invitation_rejects_duplicate_token(
        self, async_session_maker, seeded_org_role_user, store_with_session
    ):
        """The unique constraint on ``token`` is enforced by the DB."""
        from sqlalchemy.exc import IntegrityError

        org_id, role_id, inviter_id = seeded_org_role_user

        # Insert a row with a known token, then try to create another with the
        # same token by pre-seeding it directly.
        fixed_token = OrgInvitationStore.generate_token()
        async with async_session_maker() as session:
            session.add(
                OrgInvitation(
                    token=fixed_token,
                    org_id=org_id,
                    email='first@example.com',
                    role_id=role_id,
                    inviter_id=inviter_id,
                    status=OrgInvitation.STATUS_PENDING,
                    expires_at=datetime.utcnow() + timedelta(days=1),
                )
            )
            await session.commit()

        # Force create_invitation to generate a colliding token.
        with patch(
            'storage.org_invitation_store.OrgInvitationStore.generate_token',
            return_value=fixed_token,
        ):
            with pytest.raises(IntegrityError):
                await OrgInvitationStore.create_invitation(
                    org_id=org_id,
                    email='second@example.com',
                    role_id=role_id,
                    inviter_id=inviter_id,
                )


class TestGetInvitationByToken:
    """Test cases for getting invitation by token."""

    @pytest.mark.asyncio
    async def test_get_invitation_by_token_returns_invitation(
        self, async_session_maker, seeded_org_role_user, store_with_session
    ):
        """get_invitation_by_token returns the row (with eager-loaded role)."""
        org_id, role_id, inviter_id = seeded_org_role_user
        token = 'inv-test-token-12345'

        async with async_session_maker() as session:
            session.add(
                OrgInvitation(
                    token=token,
                    org_id=org_id,
                    email='test@example.com',
                    role_id=role_id,
                    inviter_id=inviter_id,
                    status=OrgInvitation.STATUS_PENDING,
                    expires_at=datetime.utcnow() + timedelta(days=1),
                )
            )
            await session.commit()

        result = await OrgInvitationStore.get_invitation_by_token(token)
        assert result is not None
        assert result.token == token
        assert result.email == 'test@example.com'
        # joinedload relationship should be populated, not raise DetachedInstanceError.
        assert result.role is not None
        assert result.role.id == role_id

    @pytest.mark.asyncio
    async def test_get_invitation_by_token_returns_none_when_not_found(
        self, store_with_session
    ):
        """get_invitation_by_token returns None when not found."""
        result = await OrgInvitationStore.get_invitation_by_token(
            'inv-nonexistent-token'
        )
        assert result is None


class TestGetPendingInvitation:
    """Test cases for getting pending invitation."""

    @pytest.mark.asyncio
    async def test_get_pending_invitation_normalizes_email(
        self, async_session_maker, seeded_org_role_user, store_with_session
    ):
        """Email normalization in the filter matches a stored lowercase row."""
        org_id, role_id, inviter_id = seeded_org_role_user
        normalized_email = 'test@example.com'

        async with async_session_maker() as session:
            session.add(
                OrgInvitation(
                    token=OrgInvitationStore.generate_token(),
                    org_id=org_id,
                    email=normalized_email,
                    role_id=role_id,
                    inviter_id=inviter_id,
                    status=OrgInvitation.STATUS_PENDING,
                    expires_at=datetime.utcnow() + timedelta(days=1),
                )
            )
            await session.commit()

        # Query with a non-normalized email; the store lowercases/strips it.
        result = await OrgInvitationStore.get_pending_invitation(
            org_id=org_id,
            email='  TEST@EXAMPLE.COM  ',
        )
        assert result is not None
        assert result.email == normalized_email

    @pytest.mark.asyncio
    async def test_get_pending_invitation_excludes_non_pending(
        self, async_session_maker, seeded_org_role_user, store_with_session
    ):
        """Accepted invitations are not returned by the pending lookup."""
        org_id, role_id, inviter_id = seeded_org_role_user

        async with async_session_maker() as session:
            session.add(
                OrgInvitation(
                    token=OrgInvitationStore.generate_token(),
                    org_id=org_id,
                    email='accepted@example.com',
                    role_id=role_id,
                    inviter_id=inviter_id,
                    status=OrgInvitation.STATUS_ACCEPTED,
                    expires_at=datetime.utcnow() + timedelta(days=1),
                )
            )
            await session.commit()

        result = await OrgInvitationStore.get_pending_invitation(
            org_id=org_id,
            email='ACCEPTED@example.com',
        )
        assert result is None


class TestUpdateInvitationStatus:
    """Test cases for updating invitation status."""

    @pytest.mark.asyncio
    async def test_update_status_sets_accepted_at_for_accepted(
        self, async_session_maker, seeded_org_role_user, store_with_session
    ):
        """accepted_at and accepted_by_user_id are persisted on acceptance."""
        org_id, role_id, inviter_id = seeded_org_role_user

        async with async_session_maker() as session:
            invitation = OrgInvitation(
                token=OrgInvitationStore.generate_token(),
                org_id=org_id,
                email='test@example.com',
                role_id=role_id,
                inviter_id=inviter_id,
                status=OrgInvitation.STATUS_PENDING,
                expires_at=datetime.utcnow() + timedelta(days=1),
            )
            session.add(invitation)
            await session.commit()
            await session.refresh(invitation)
            invitation_id = invitation.id

        accepter_id = uuid4()
        updated = await OrgInvitationStore.update_invitation_status(
            invitation_id=invitation_id,
            status=OrgInvitation.STATUS_ACCEPTED,
            accepted_by_user_id=accepter_id,
        )

        assert updated is not None
        assert updated.accepted_at is not None
        assert updated.accepted_by_user_id == accepter_id
        assert updated.status == OrgInvitation.STATUS_ACCEPTED

        # Confirm the change persisted to the DB.
        async with async_session_maker() as session:
            row = await session.get(OrgInvitation, invitation_id)
            assert row is not None
            assert row.status == OrgInvitation.STATUS_ACCEPTED
            assert row.accepted_at is not None

    @pytest.mark.asyncio
    async def test_update_status_returns_none_when_not_found(self, store_with_session):
        """Update returns None when the invitation does not exist."""
        result = await OrgInvitationStore.update_invitation_status(
            invitation_id=999999,
            status=OrgInvitation.STATUS_ACCEPTED,
        )
        assert result is None


class TestMarkExpiredIfNeeded:
    """Test cases for marking expired invitations."""

    @pytest.mark.asyncio
    async def test_marks_expired_when_pending_and_past_expiry(self):
        """Test that pending expired invitations are marked as expired."""
        mock_invitation = MagicMock(spec=OrgInvitation)
        mock_invitation.id = 1
        mock_invitation.status = OrgInvitation.STATUS_PENDING
        mock_invitation.expires_at = datetime.utcnow() - timedelta(days=1)

        with patch.object(
            OrgInvitationStore,
            'update_invitation_status',
            new_callable=AsyncMock,
        ) as mock_update:
            result = await OrgInvitationStore.mark_expired_if_needed(mock_invitation)

            assert result is True
            mock_update.assert_called_once_with(1, OrgInvitation.STATUS_EXPIRED)

    @pytest.mark.asyncio
    async def test_does_not_mark_when_not_expired(self):
        """Test that non-expired invitations are not marked."""
        mock_invitation = MagicMock(spec=OrgInvitation)
        mock_invitation.id = 1
        mock_invitation.status = OrgInvitation.STATUS_PENDING
        mock_invitation.expires_at = datetime.utcnow() + timedelta(days=1)

        with patch.object(
            OrgInvitationStore,
            'update_invitation_status',
            new_callable=AsyncMock,
        ) as mock_update:
            result = await OrgInvitationStore.mark_expired_if_needed(mock_invitation)

            assert result is False
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_mark_when_not_pending(self):
        """Test that non-pending invitations are not marked even if expired."""
        mock_invitation = MagicMock(spec=OrgInvitation)
        mock_invitation.id = 1
        mock_invitation.status = OrgInvitation.STATUS_ACCEPTED
        mock_invitation.expires_at = datetime.utcnow() - timedelta(days=1)

        with patch.object(
            OrgInvitationStore,
            'update_invitation_status',
            new_callable=AsyncMock,
        ) as mock_update:
            result = await OrgInvitationStore.mark_expired_if_needed(mock_invitation)

            assert result is False
            mock_update.assert_not_called()
