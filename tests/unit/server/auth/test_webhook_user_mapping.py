"""Mapping the git provider user behind a webhook to an OpenHands user:
``auth_provider_users`` first, then Keycloak if it is configured.

Runs against the test database.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openhands.app_server.integrations.service_types import ProviderType
from server.auth.token_manager import TokenManager
from storage.auth_provider_user_store import AuthProviderUserStore


@pytest.mark.asyncio
async def test_a_linked_git_account_maps_to_its_user_without_keycloak(
    no_keycloak, create_git_provider, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await AuthProviderUserStore().link(
        oauth_provider_id=gitlab.id, user_id=user.id, external_subject_id='123'
    )

    # GitLab webhook payloads carry the user id as a number.
    user_id = await TokenManager().get_user_id_from_idp_user_id(
        123, ProviderType.GITLAB
    )

    assert user_id == str(user.id)


@pytest.mark.asyncio
async def test_an_unlinked_account_maps_to_nobody_without_keycloak(
    no_keycloak, create_git_provider
):
    await create_git_provider(ProviderType.GITLAB)

    with patch('server.auth.token_manager.KEYCLOAK_SERVER_URL', ''):
        user_id = await TokenManager().get_user_id_from_idp_user_id(
            '123', ProviderType.GITLAB
        )

    assert user_id is None


@pytest.mark.asyncio
async def test_an_unlinked_account_is_looked_up_in_keycloak_when_configured(
    create_git_provider,
):
    await create_git_provider(ProviderType.GITLAB)
    keycloak_admin = MagicMock()
    keycloak_admin.a_get_users = AsyncMock(return_value=[{'id': 'kc-user'}])

    with (
        patch('server.auth.token_manager.KEYCLOAK_SERVER_URL', 'http://keycloak:8080'),
        patch(
            'server.auth.token_manager.get_keycloak_admin',
            return_value=keycloak_admin,
        ),
    ):
        user_id = await TokenManager().get_user_id_from_idp_user_id(
            '123', ProviderType.GITLAB
        )

    assert user_id == 'kc-user'
    keycloak_admin.a_get_users.assert_awaited_once_with({'q': 'gitlab_id:123'})


@pytest.mark.asyncio
async def test_a_link_for_another_provider_type_does_not_match(
    no_keycloak, create_git_provider, create_user
):
    github = await create_git_provider(ProviderType.GITHUB)
    await create_git_provider(ProviderType.GITLAB)
    await AuthProviderUserStore().link(
        oauth_provider_id=github.id,
        user_id=create_user().id,
        external_subject_id='123',
    )

    with patch('server.auth.token_manager.KEYCLOAK_SERVER_URL', ''):
        user_id = await TokenManager().get_user_id_from_idp_user_id(
            '123', ProviderType.GITLAB
        )

    assert user_id is None


@pytest.mark.asyncio
async def test_a_login_provider_link_does_not_match(
    no_keycloak, create_git_provider, create_user
):
    gitlab_login = await create_git_provider(ProviderType.GITLAB, is_idp=True)
    await AuthProviderUserStore().link(
        oauth_provider_id=gitlab_login.id,
        user_id=create_user().id,
        external_subject_id='123',
    )

    with patch('server.auth.token_manager.KEYCLOAK_SERVER_URL', ''):
        user_id = await TokenManager().get_user_id_from_idp_user_id(
            '123', ProviderType.GITLAB
        )

    assert user_id is None


@pytest.mark.asyncio
async def test_a_webhook_senders_token_comes_from_oauth_tokens(
    no_keycloak, create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB)
    user = create_user()
    await AuthProviderUserStore().link(
        oauth_provider_id=gitlab.id, user_id=user.id, external_subject_id='123'
    )
    await store_oauth_token(user.id, gitlab, 'glpat-linked')

    token = await TokenManager().get_idp_token_from_idp_user_id(
        '123', ProviderType.GITLAB
    )

    assert token == 'glpat-linked'
