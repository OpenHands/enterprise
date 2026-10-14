"""Unit tests for org product-usability lifecycle gates."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from server.auth.org_access import OrgNotUsableError, assert_org_usable_for_product


@pytest.fixture(autouse=True)
def _super_admin_enabled():
    """The gates apply only while the Super Admin dashboard is on."""
    with patch('server.auth.org_access.ENABLE_SUPER_ADMIN', True):
        yield


@pytest.mark.asyncio
async def test_assert_skips_every_lookup_while_super_admin_is_off():
    """With the flag off nothing can suspend an org or a membership, so the
    helper neither reads the stores nor refuses."""
    is_super_admin = AsyncMock(return_value=False)
    get_org = AsyncMock(return_value=MagicMock(status='suspended'))
    get_member = AsyncMock(return_value=MagicMock(status='inactive'))

    with (
        patch('server.auth.org_access.ENABLE_SUPER_ADMIN', False),
        patch('server.auth.authorization.is_instance_super_admin', is_super_admin),
        patch('storage.org_store.OrgStore.get_org_by_id', get_org),
        patch('storage.org_member_store.OrgMemberStore.get_org_member', get_member),
    ):
        await assert_org_usable_for_product(uuid4(), user_id=str(uuid4()))

    is_super_admin.assert_not_awaited()
    get_org.assert_not_awaited()
    get_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_assert_passes_for_active_org_and_member():
    org_id = uuid4()
    user_id = str(uuid4())
    org = MagicMock(status='active')
    member = MagicMock(status='active')

    with (
        patch(
            'server.auth.authorization.is_instance_super_admin',
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            'storage.org_store.OrgStore.get_org_by_id',
            new_callable=AsyncMock,
            return_value=org,
        ),
        patch(
            'storage.org_member_store.OrgMemberStore.get_org_member',
            new_callable=AsyncMock,
            return_value=member,
        ),
    ):
        await assert_org_usable_for_product(org_id, user_id=user_id)


@pytest.mark.asyncio
async def test_assert_raises_for_suspended_org():
    org_id = uuid4()
    org = MagicMock(status='suspended')

    with patch(
        'storage.org_store.OrgStore.get_org_by_id',
        new_callable=AsyncMock,
        return_value=org,
    ):
        with pytest.raises(OrgNotUsableError, match='suspended'):
            await assert_org_usable_for_product(org_id, user_id=None)


@pytest.mark.asyncio
async def test_assert_passes_suspended_org_for_instance_super_admin():
    org_id = uuid4()
    user_id = str(uuid4())

    with patch(
        'server.auth.authorization.is_instance_super_admin',
        new_callable=AsyncMock,
        return_value=True,
    ):
        await assert_org_usable_for_product(org_id, user_id=user_id)


@pytest.mark.asyncio
async def test_assert_still_blocks_super_admin_when_bypass_disabled():
    org_id = uuid4()
    user_id = str(uuid4())
    org = MagicMock(status='suspended')

    with (
        patch(
            'server.auth.authorization.is_instance_super_admin',
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            'storage.org_store.OrgStore.get_org_by_id',
            new_callable=AsyncMock,
            return_value=org,
        ),
    ):
        with pytest.raises(OrgNotUsableError, match='suspended'):
            await assert_org_usable_for_product(
                org_id,
                user_id=user_id,
                allow_instance_super_admin=False,
            )


@pytest.mark.asyncio
async def test_assert_raises_for_inactive_membership():
    org_id = uuid4()
    user_id = str(uuid4())
    org = MagicMock(status='active')
    member = MagicMock(status='inactive')

    with (
        patch(
            'server.auth.authorization.is_instance_super_admin',
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            'storage.org_store.OrgStore.get_org_by_id',
            new_callable=AsyncMock,
            return_value=org,
        ),
        patch(
            'storage.org_member_store.OrgMemberStore.get_org_member',
            new_callable=AsyncMock,
            return_value=member,
        ),
    ):
        with pytest.raises(OrgNotUsableError, match='membership is suspended'):
            await assert_org_usable_for_product(org_id, user_id=user_id)


@pytest.mark.asyncio
async def test_assert_noop_when_org_missing():
    org_id = uuid4()

    with (
        patch(
            'server.auth.authorization.is_instance_super_admin',
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            'storage.org_store.OrgStore.get_org_by_id',
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        await assert_org_usable_for_product(org_id, user_id=str(uuid4()))
