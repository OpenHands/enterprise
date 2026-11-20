from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from server.services.integrated_idp_user_service import (
    IntegratedIdpUserDisabledError,
    create_or_update_integrated_idp_password_user,
)


@pytest.mark.asyncio
async def test_create_password_user_suppresses_auto_superadmin():
    provider = MagicMock(id=7)
    user = MagicMock(id=uuid4(), is_disabled=False, deletion_pending=False)
    link_store = MagicMock()
    link_store.link = AsyncMock()

    with (
        patch(
            'server.services.integrated_idp_user_service.find_integrated_idp_user',
            new=AsyncMock(return_value=None),
        ),
        patch(
            'server.services.integrated_idp_user_service.UserStore.create_user',
            new=AsyncMock(return_value=user),
        ) as mock_create,
        patch(
            'server.services.integrated_idp_user_service.UserStore.set_password_hash',
            new=AsyncMock(),
        ) as mock_set_hash,
        patch(
            'server.services.integrated_idp_user_service.AuthProviderUserStore',
            return_value=link_store,
        ),
    ):
        result = await create_or_update_integrated_idp_password_user(
            provider=provider,
            email='Test@Example.COM',
            password='password123',
        )

    assert result.user is user
    assert result.created is True
    mock_create.assert_awaited_once()
    assert mock_create.call_args.kwargs['suppress_auto_superadmin'] is True
    assert mock_create.call_args.args[1]['email'] == 'test@example.com'
    mock_set_hash.assert_awaited_once()
    link_store.link.assert_awaited_once_with(
        7, user.id, 'test@example.com', 'test@example.com'
    )


@pytest.mark.asyncio
async def test_create_password_user_refuses_disabled_existing_user():
    provider = MagicMock(id=7)
    user = MagicMock(is_disabled=True, deletion_pending=False)

    with patch(
        'server.services.integrated_idp_user_service.find_integrated_idp_user',
        new=AsyncMock(return_value=user),
    ):
        with pytest.raises(IntegratedIdpUserDisabledError):
            await create_or_update_integrated_idp_password_user(
                provider=provider,
                email='test@example.com',
                password='password123',
            )
