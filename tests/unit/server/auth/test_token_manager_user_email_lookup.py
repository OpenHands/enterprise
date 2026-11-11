"""Resolving a user from an email address, with and without Keycloak."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.auth.token_manager import TokenManager


@pytest.fixture
def token_manager(async_session_maker) -> TokenManager:
    with patch('server.auth.token_manager.a_session_maker', async_session_maker):
        yield TokenManager()


@pytest.fixture
def no_keycloak_server():
    with patch('server.auth.token_manager.KEYCLOAK_SERVER_URL', ''):
        yield


class TestOAuthV2LoginOn:
    @pytest.mark.asyncio
    async def test_finds_user_who_is_not_in_keycloak(
        self,
        token_manager,
        create_user,
        oauth_v2_stores,
        oauth_v2_login_on,
        no_keycloak,
    ):
        user = create_user(email='bob@acme.com')

        assert await token_manager.get_user_id_from_user_email('Bob@Acme.com') == str(
            user.id
        )

    @pytest.mark.asyncio
    async def test_matches_the_whole_address(
        self,
        token_manager,
        create_user,
        oauth_v2_stores,
        oauth_v2_login_on,
        no_keycloak,
        no_keycloak_server,
    ):
        create_user(email='bob@acme.com.au')

        assert await token_manager.get_user_id_from_user_email('bob@acme.com') is None

    @pytest.mark.asyncio
    async def test_refuses_an_address_shared_by_several_users(
        self,
        token_manager,
        create_user,
        oauth_v2_stores,
        oauth_v2_login_on,
        no_keycloak,
    ):
        create_user(email='bob@acme.com')
        create_user(email='bob@acme.com')

        assert await token_manager.get_user_id_from_user_email('bob@acme.com') is None

    @pytest.mark.asyncio
    async def test_unknown_address_without_keycloak_server_is_none(
        self,
        token_manager,
        oauth_v2_stores,
        oauth_v2_login_on,
        no_keycloak,
        no_keycloak_server,
    ):
        assert await token_manager.get_user_id_from_user_email('who@acme.com') is None

    @pytest.mark.asyncio
    async def test_unknown_address_falls_back_to_keycloak(
        self, token_manager, oauth_v2_stores, oauth_v2_login_on
    ):
        admin = MagicMock(
            a_get_users=AsyncMock(
                return_value=[{'id': 'kc-id', 'email': 'who@acme.com'}]
            )
        )
        with (
            patch('server.auth.token_manager.KEYCLOAK_SERVER_URL', 'https://kc.test'),
            patch('server.auth.token_manager.get_keycloak_admin', return_value=admin),
        ):
            assert (
                await token_manager.get_user_id_from_user_email('who@acme.com')
                == 'kc-id'
            )


class TestOAuthV2LoginOff:
    @pytest.mark.asyncio
    async def test_asks_keycloak(
        self, token_manager, create_user, oauth_v2_stores, oauth_v2_login_off
    ):
        create_user(email='bob@acme.com')
        admin = MagicMock(
            a_get_users=AsyncMock(
                return_value=[{'id': 'kc-id', 'email': 'bob@acme.com'}]
            )
        )
        with patch('server.auth.token_manager.get_keycloak_admin', return_value=admin):
            assert (
                await token_manager.get_user_id_from_user_email('bob@acme.com')
                == 'kc-id'
            )
