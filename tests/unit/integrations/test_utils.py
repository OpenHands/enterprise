"""Tests for enterprise integrations utils module."""

from integrations.utils import (
    CONVERSATION_URL,
    HOST_URL,
    get_jira_dc_relink_message,
    get_session_expired_message,
    get_user_not_found_message,
)


class TestGetSessionExpiredMessage:
    """Test cases for get_session_expired_message function."""

    def test_message_with_username_contains_at_prefix(self):
        """Test that the message contains the username with @ prefix."""
        result = get_session_expired_message('testuser')
        assert '@testuser' in result

    def test_message_with_username_contains_session_expired_text(self):
        """Test that the message contains session expired text."""
        result = get_session_expired_message('testuser')
        assert 'session has expired' in result

    def test_message_with_username_contains_login_instruction(self):
        """Test that the message contains login instruction."""
        result = get_session_expired_message('testuser')
        assert 'login again' in result

    def test_message_with_username_contains_host_url(self):
        """Test that the message contains the OpenHands Cloud URL."""
        result = get_session_expired_message('testuser')
        assert HOST_URL in result
        assert 'OpenHands Cloud' in result

    def test_different_usernames(self):
        """Test that different usernames produce different messages."""
        result1 = get_session_expired_message('user1')
        result2 = get_session_expired_message('user2')
        assert '@user1' in result1
        assert '@user2' in result2
        assert '@user1' not in result2
        assert '@user2' not in result1

    def test_message_without_username_contains_session_expired_text(self):
        """Test that the message without username contains session expired text."""
        result = get_session_expired_message()
        assert 'session has expired' in result

    def test_message_without_username_contains_login_instruction(self):
        """Test that the message without username contains login instruction."""
        result = get_session_expired_message()
        assert 'login again' in result

    def test_message_without_username_contains_host_url(self):
        """Test that the message without username contains the OpenHands Cloud URL."""
        result = get_session_expired_message()
        assert HOST_URL in result
        assert 'OpenHands Cloud' in result

    def test_message_without_username_does_not_contain_at_prefix(self):
        """Test that the message without username does not contain @ prefix."""
        result = get_session_expired_message()
        assert not result.startswith('@')
        assert 'Your session' in result

    def test_message_with_none_username(self):
        """Test that passing None explicitly works the same as no argument."""
        result = get_session_expired_message(None)
        assert not result.startswith('@')
        assert 'Your session' in result


class TestGetUserNotFoundMessage:
    """Test cases for get_user_not_found_message function.

    This function is used to notify users when they try to use OpenHands features
    but haven't created an OpenHands account yet (no Keycloak account exists).
    """

    def test_message_with_username_contains_at_prefix(self):
        """Test that the message contains the username with @ prefix."""
        result = get_user_not_found_message('testuser')
        assert '@testuser' in result

    def test_message_with_username_contains_sign_up_text(self):
        """Test that the message contains sign up text."""
        result = get_user_not_found_message('testuser')
        assert "haven't created an OpenHands account" in result

    def test_message_with_username_contains_sign_up_instruction(self):
        """Test that the message contains sign up instruction."""
        result = get_user_not_found_message('testuser')
        assert 'sign up' in result.lower()

    def test_message_with_username_contains_host_url(self):
        """Test that the message contains the OpenHands Cloud URL."""
        result = get_user_not_found_message('testuser')
        assert HOST_URL in result
        assert 'OpenHands Cloud' in result

    def test_different_usernames(self):
        """Test that different usernames produce different messages."""
        result1 = get_user_not_found_message('user1')
        result2 = get_user_not_found_message('user2')
        assert '@user1' in result1
        assert '@user2' in result2
        assert '@user1' not in result2
        assert '@user2' not in result1

    def test_message_without_username_contains_sign_up_text(self):
        """Test that the message without username contains sign up text."""
        result = get_user_not_found_message()
        assert "haven't created an OpenHands account" in result

    def test_message_without_username_contains_sign_up_instruction(self):
        """Test that the message without username contains sign up instruction."""
        result = get_user_not_found_message()
        assert 'sign up' in result.lower()

    def test_message_without_username_contains_host_url(self):
        """Test that the message without username contains the OpenHands Cloud URL."""
        result = get_user_not_found_message()
        assert HOST_URL in result
        assert 'OpenHands Cloud' in result

    def test_message_without_username_does_not_contain_at_prefix(self):
        """Test that the message without username does not contain @ prefix."""
        result = get_user_not_found_message()
        assert not result.startswith('@')
        assert 'It looks like' in result

    def test_message_with_none_username(self):
        """Test that passing None explicitly works the same as no argument."""
        result = get_user_not_found_message(None)
        assert not result.startswith('@')
        assert 'It looks like' in result


class TestGetJiraDcRelinkMessage:
    """Test cases for get_jira_dc_relink_message function."""

    def test_message_with_display_name_uses_jira_wiki_link(self):
        result = get_jira_dc_relink_message('Alona')

        assert result.startswith('Hi Alona,')
        assert f'[OpenHands Cloud|{HOST_URL}]' in result
        assert 're-link' in result
        assert 'Settings' in result
        assert 'Integrations' in result

    def test_message_without_display_name_is_generic(self):
        result = get_jira_dc_relink_message()

        assert not result.startswith('Hi ')
        assert f'[OpenHands Cloud|{HOST_URL}]' in result
        assert 'your Jira workspace link has expired' in result


class TestConversationUrl:
    """Test cases for the CONVERSATION_URL template.

    Integration "track my progress" messages and V1 error fallbacks all
    link to the conversation through this template. It must point at Agent
    Canvas (/canvas) rather than the retired legacy SPA route.
    """

    def test_url_points_at_canvas_route(self):
        assert CONVERSATION_URL == f'{HOST_URL}/canvas/conversations/{{}}'

    def test_url_does_not_use_legacy_route(self):
        assert '/conversations/' not in CONVERSATION_URL.replace(
            '/canvas/conversations/', ''
        )

    def test_url_formats_with_conversation_id(self):
        formatted = CONVERSATION_URL.format('conv-123')
        assert formatted == f'{HOST_URL}/canvas/conversations/conv-123'
