"""Unit tests for org product-usability lifecycle gates."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from server.auth.org_access import OrgNotUsableError, assert_org_usable_for_product


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
