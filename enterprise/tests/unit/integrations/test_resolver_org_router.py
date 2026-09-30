"""Tests for resolver org routing logic.

Tests the resolve_org_for_repo function which determines which OpenHands
organization workspace a resolver conversation should be created in.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from server.auth.org_access import OrgNotUsableError

CLAIMING_ORG_ID = UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
USER_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'

# Patch at module level where the names are looked up
_CLAIM_STORE = 'enterprise.integrations.resolver_org_router.OrgGitClaimStore'
_MEMBER_STORE = 'enterprise.integrations.resolver_org_router.OrgMemberStore'
_ASSERT_USABLE = 'enterprise.integrations.resolver_org_router.assert_org_usable_for_product'


@pytest.fixture(autouse=True)
def mock_stores():
    """Mock OrgGitClaimStore, OrgMemberStore, and the lifecycle guard."""
    with (
        patch(_CLAIM_STORE) as mock_claim_store,
        patch(_MEMBER_STORE) as mock_member_store,
        patch(_ASSERT_USABLE, new_callable=AsyncMock) as mock_assert,
    ):
        mock_claim_store.get_claim_by_provider_and_git_org = AsyncMock(
            return_value=None
        )
        mock_member_store.get_org_member = AsyncMock(return_value=None)
        yield mock_claim_store, mock_member_store, mock_assert


@pytest.mark.asyncio
async def test_returns_org_id_when_claimed_and_user_is_member(mock_stores):
    """When the git org is claimed and the user is a member, return the claiming org's ID."""
    from enterprise.integrations.resolver_org_router import resolve_org_for_repo

    mock_claim_store, mock_member_store, mock_assert = mock_stores

    # Arrange
    claim = MagicMock()
    claim.org_id = CLAIMING_ORG_ID
    mock_claim_store.get_claim_by_provider_and_git_org.return_value = claim
    mock_member_store.get_org_member.return_value = MagicMock()  # member exists

    # Act
    result = await resolve_org_for_repo('github', 'OpenHands/foo', USER_ID)

    # Assert
    assert result == CLAIMING_ORG_ID
    mock_claim_store.get_claim_by_provider_and_git_org.assert_called_once_with(
        'github', 'openhands'
    )
    mock_member_store.get_org_member.assert_called_once_with(
        CLAIMING_ORG_ID, UUID(USER_ID)
    )
    mock_assert.assert_awaited_once_with(
        CLAIMING_ORG_ID,
        user_id=USER_ID,
        allow_instance_super_admin=False,
    )


@pytest.mark.asyncio
async def test_returns_none_when_claimed_but_user_not_member(mock_stores):
    """When the git org is claimed but user is not a member, return None."""
    from enterprise.integrations.resolver_org_router import resolve_org_for_repo

    mock_claim_store, mock_member_store, _ = mock_stores

    # Arrange
    claim = MagicMock()
    claim.org_id = CLAIMING_ORG_ID
    mock_claim_store.get_claim_by_provider_and_git_org.return_value = claim
    mock_member_store.get_org_member.return_value = None

    # Act
    result = await resolve_org_for_repo('github', 'OpenHands/foo', USER_ID)

    # Assert
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_no_claim_exists(mock_stores):
    """When no org has claimed the git organization, return None."""
    from enterprise.integrations.resolver_org_router import resolve_org_for_repo

    mock_claim_store, _, mock_assert = mock_stores
    mock_claim_store.get_claim_by_provider_and_git_org.return_value = None

    # Act
    result = await resolve_org_for_repo('github', 'UnclaimedOrg/repo', USER_ID)

    # Assert
    assert result is None
    mock_claim_store.get_claim_by_provider_and_git_org.assert_called_once_with(
        'github', 'unclaimedorg'
    )
    mock_assert.assert_not_awaited()


@pytest.mark.asyncio
async def test_extracts_git_org_lowercase_from_repo_name(mock_stores):
    """The git org is extracted from repo name and lowercased for claim lookup."""
    from enterprise.integrations.resolver_org_router import resolve_org_for_repo

    mock_claim_store, _, _ = mock_stores

    # Act
    await resolve_org_for_repo('github', 'MyOrg/some-repo', USER_ID)

    # Assert
    mock_claim_store.get_claim_by_provider_and_git_org.assert_called_once_with(
        'github', 'myorg'
    )


@pytest.mark.asyncio
async def test_returns_org_id_without_membership_check_when_no_user_id(mock_stores):
    """When user_id is None, skip membership check and return org_id if claim exists."""
    from enterprise.integrations.resolver_org_router import resolve_org_for_repo

    mock_claim_store, mock_member_store, mock_assert = mock_stores

    # Arrange
    claim = MagicMock()
    claim.org_id = CLAIMING_ORG_ID
    mock_claim_store.get_claim_by_provider_and_git_org.return_value = claim

    # Act - no user_id provided
    result = await resolve_org_for_repo('github', 'OpenHands/foo')

    # Assert
    assert result == CLAIMING_ORG_ID
    mock_claim_store.get_claim_by_provider_and_git_org.assert_called_once_with(
        'github', 'openhands'
    )
    # Membership check should NOT be called
    mock_member_store.get_org_member.assert_not_called()
    mock_assert.assert_awaited_once_with(
        CLAIMING_ORG_ID,
        user_id=None,
        allow_instance_super_admin=False,
    )


@pytest.mark.asyncio
async def test_returns_none_when_no_claim_and_no_user_id(mock_stores):
    """When no claim exists and no user_id, return None."""
    from enterprise.integrations.resolver_org_router import resolve_org_for_repo

    mock_claim_store, mock_member_store, _ = mock_stores
    mock_claim_store.get_claim_by_provider_and_git_org.return_value = None

    # Act - no user_id provided
    result = await resolve_org_for_repo('github', 'UnclaimedOrg/repo')

    # Assert
    assert result is None
    mock_member_store.get_org_member.assert_not_called()


@pytest.mark.asyncio
async def test_raises_when_claimed_org_is_suspended(mock_stores):
    """Suspended claimed orgs must abort — do not fall back to personal."""
    from enterprise.integrations.resolver_org_router import resolve_org_for_repo

    mock_claim_store, mock_member_store, mock_assert = mock_stores
    claim = MagicMock()
    claim.org_id = CLAIMING_ORG_ID
    mock_claim_store.get_claim_by_provider_and_git_org.return_value = claim
    mock_assert.side_effect = OrgNotUsableError('Organization is suspended')

    with pytest.raises(OrgNotUsableError, match='suspended'):
        await resolve_org_for_repo('github', 'OpenHands/foo', USER_ID)

    mock_member_store.get_org_member.assert_not_called()
