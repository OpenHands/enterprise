"""A Slack-linked user from an OAuth v2 login has no Keycloak offline token."""

from unittest.mock import patch

import pytest

from integrations.slack.slack_manager import SlackManager
from server.auth.token_manager import TokenManager
from storage.slack_user import SlackUser


@pytest.mark.asyncio
async def test_access_token_without_offline_token_does_not_raise(
    create_user,
    oauth_v2_stores,
    oauth_v2_login_on,
    no_keycloak,
    async_session_maker,
):
    user = create_user()
    async with async_session_maker() as session:
        session.add(
            SlackUser(
                keycloak_user_id=str(user.id),
                org_id=user.current_org_id,
                slack_user_id='U123',
                slack_display_name='Slackie',
                team_id='T123',
            )
        )
        await session.commit()
    manager = SlackManager(TokenManager())

    with patch('integrations.slack.slack_manager.a_session_maker', async_session_maker):
        _, user_auth = await manager.authenticate_user('U123')

    assert user_auth is not None
    assert await user_auth.get_access_token() is None
