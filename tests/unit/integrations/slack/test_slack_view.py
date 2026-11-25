"""Tests for the Slack view classes and V1 conversation handling.

Focuses on V1 conversation scenarios:
1. V1 conversation creation
2. Paused sandbox resumption for V1 conversations
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from jinja2 import DictLoader, Environment
from slack_sdk.errors import SlackApiError

from integrations.slack.slack_errors import SlackError, SlackErrorCode
from integrations.slack.slack_types import SlackTrigger, StartingConvoException
from integrations.slack.slack_view import (
    SlackNewConversationView,
    SlackUpdateExistingConversationView,
    fetch_exact_message,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartRequest,
    AppConversationStartTask,
    AppConversationStartTaskStatus,
)
from openhands.app_server.sandbox.sandbox_models import SandboxStatus
from openhands.app_server.user_auth.user_auth import UserAuth
from openhands.app_server.utils.slack_config import (
    SlackContextMode,
    get_slack_context_mode,
)
from storage.slack_conversation import SlackConversation
from storage.slack_user import SlackUser

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_jinja_env():
    """Create a mock Jinja environment with test templates."""
    templates = {
        'user_message_conversation_instructions.j2': 'Previous messages: {{ messages|join(", ") }}\nUser: {{ username }}\nURL: {{ conversation_url }}'
    }
    return Environment(loader=DictLoader(templates))


@pytest.fixture
def mock_slack_user():
    """Create a mock SlackUser."""
    user = SlackUser()
    user.slack_user_id = 'U1234567890'
    user.keycloak_user_id = 'test-user-123'
    user.slack_display_name = 'Test User'
    return user


@pytest.fixture
def mock_user_auth():
    """Create a mock UserAuth."""
    auth = MagicMock(spec=UserAuth)
    auth.get_provider_tokens = AsyncMock(return_value={})
    auth.get_secrets = AsyncMock(return_value=MagicMock(custom_secrets={}))
    return auth


@pytest.fixture
def slack_new_conversation_view(mock_slack_user, mock_user_auth):
    """Create a SlackNewConversationView instance."""
    return SlackNewConversationView(
        bot_access_token='xoxb-test-token',
        user_msg='Hello OpenHands!',
        slack_user_id='U1234567890',
        slack_to_openhands_user=mock_slack_user,
        saas_user_auth=mock_user_auth,
        channel_id='C1234567890',
        message_ts='1234567890.123456',
        thread_ts=None,
        selected_repo='owner/repo',
        should_extract=True,
        send_summary_instruction=True,
        conversation_id='',
        team_id='T1234567890',
    )


@pytest.fixture
def slack_update_conversation_view_v1(mock_slack_user, mock_user_auth):
    """Create a SlackUpdateExistingConversationView instance for V1."""
    conversation_id = '12345678-1234-5678-1234-567812345678'
    mock_conversation = SlackConversation(
        conversation_id=conversation_id,
        channel_id='C1234567890',
        keycloak_user_id='test-user-123',
        parent_id='1234567890.123456',
        v1_enabled=True,
    )
    return SlackUpdateExistingConversationView(
        bot_access_token='xoxb-test-token',
        user_msg='Follow up message',
        slack_user_id='U1234567890',
        slack_to_openhands_user=mock_slack_user,
        saas_user_auth=mock_user_auth,
        channel_id='C1234567890',
        message_ts='1234567890.123457',
        thread_ts='1234567890.123456',
        selected_repo=None,
        should_extract=True,
        send_summary_instruction=True,
        conversation_id=conversation_id,
        slack_conversation=mock_conversation,
        team_id='T1234567890',
    )


# ---------------------------------------------------------------------------


def test_build_message_content_includes_slack_attachment_images(
    slack_new_conversation_view,
):
    slack_new_conversation_view.attachment_image_urls = [
        'data:image/png;base64,aW1hZ2UtYnl0ZXM='
    ]

    content = slack_new_conversation_view._build_message_content('hello')

    assert len(content) == 2
    assert content[0].type == 'text'
    assert content[0].text == 'hello'
    assert content[1].type == 'image'
    assert content[1].image_urls == ['data:image/png;base64,aW1hZ2UtYnl0ZXM=']


def test_format_slack_message_context_ignores_historical_attachments(
    slack_new_conversation_view,
):
    with patch(
        'integrations.slack.slack_view.collect_message_attachment_content'
    ) as mock_collect_attachment_content:
        context = slack_new_conversation_view._format_slack_message_context(
            {
                'text': 'Earlier thread message',
                'files': [
                    {
                        'title': 'earlier-screenshot.png',
                        'mimetype': 'image/png',
                    }
                ],
            }
        )

    assert context == 'Earlier thread message'
    assert slack_new_conversation_view.attachment_image_urls == []
    mock_collect_attachment_content.assert_not_called()


@pytest.fixture
def slack_history_client():
    with patch('integrations.slack.slack_view.WebClient') as client:
        yield client.return_value


def test_slack_context_mode_default_and_invalid_configuration(monkeypatch):
    monkeypatch.delenv('SLACK_CONTEXT_MODE', raising=False)
    assert get_slack_context_mode() == SlackContextMode.LEGACY
    monkeypatch.setenv('SLACK_CONTEXT_MODE', 'unknown')
    with pytest.raises(ValueError, match='SLACK_CONTEXT_MODE'):
        get_slack_context_mode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('mode', 'thread_ts'),
    [
        (SlackContextMode.MENTION_ONLY, None),
        (SlackContextMode.MENTION_ONLY, '1234567890.000001'),
        (SlackContextMode.REQUESTER_ONLY, None),
    ],
)
async def test_restricted_mention_reads_no_surrounding_history(
    mode, thread_ts, slack_new_conversation_view, mock_jinja_env, slack_history_client
):
    view = slack_new_conversation_view
    view.thread_ts = thread_ts
    view.trigger = SlackTrigger(
        text='<@UOTHER> <@UBOT> Summarize the sample report',
        bot_user_id='UBOT',
        file_ids=['FTEST'],
    )
    with (
        patch('integrations.slack.slack_view.SLACK_CONTEXT_MODE', mode),
        patch.object(
            view,
            '_process_attachment_content_for_message',
            return_value=['- sample.txt'],
        ) as collect,
    ):
        prompt, system = await view._get_instructions(mock_jinja_env)

    assert '<@UOTHER>' in prompt
    assert '<@UBOT>' not in prompt
    assert 'Summarize the sample report' in prompt
    assert 'sample.txt' in prompt
    assert 'Summarize the sample report' not in system
    collect.assert_called_once_with({'files': [{'id': 'FTEST'}]})
    slack_history_client.conversations_history.assert_not_called()
    slack_history_client.conversations_replies.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('use_thread', [False, True])
async def test_legacy_context_keeps_earlier_messages_in_system_instructions(
    use_thread, slack_new_conversation_view, mock_jinja_env, slack_history_client
):
    view = slack_new_conversation_view
    if use_thread:
        view.thread_ts = '1234567890.000001'
    view.trigger = SlackTrigger(text='Summarize the sample report')
    fetch = (
        slack_history_client.conversations_replies
        if use_thread
        else slack_history_client.conversations_history
    )
    fetch.return_value = {
        'messages': [
            {'ts': view.message_ts, 'user': view.slack_user_id, 'text': 'Stale copy'},
            {'ts': '1234567890.000002', 'user': 'UPEER', 'text': 'Second note'},
            {'ts': '1234567890.000001', 'user': 'UPEER', 'text': 'First note'},
        ]
    }
    with patch(
        'integrations.slack.slack_view.SLACK_CONTEXT_MODE', SlackContextMode.LEGACY
    ):
        prompt, system = await view._get_instructions(mock_jinja_env)

    assert prompt == 'Summarize the sample report'
    assert 'Previous messages: First note, Second note' in system
    assert 'Stale copy' not in system
    assert fetch.call_args.kwargs['limit'] == 21


@pytest.mark.asyncio
async def test_requester_history_filters_authors_and_keeps_verified_trigger_when_truncated(
    slack_new_conversation_view, mock_jinja_env, slack_history_client
):
    view = slack_new_conversation_view
    view.thread_ts = '1234567890.000001'
    view.trigger = SlackTrigger(text='Summarize the sample report')
    slack_history_client.conversations_replies.return_value = {
        'has_more': True,
        'messages': [
            {
                'ts': '1234567890.000004',
                'user': view.slack_user_id,
                'text': 'Second note',
            },
            {'ts': '1234567890.000002', 'user': 'UPEER', 'text': 'Peer note'},
            {
                'ts': '1234567890.000003',
                'user': view.slack_user_id,
                'text': 'First note',
                'files': [{'id': 'FHISTORY'}],
            },
            {
                'ts': '1234567890.000005',
                'user': view.slack_user_id,
                'bot_id': 'BTEST',
                'text': 'Bot note',
            },
            {
                'ts': '1234567890.999999',
                'user': view.slack_user_id,
                'text': 'Later note',
            },
        ],
    }
    with patch(
        'integrations.slack.slack_view.SLACK_CONTEXT_MODE',
        SlackContextMode.REQUESTER_ONLY,
    ):
        prompt, system = await view._get_instructions(mock_jinja_env)

    assert prompt == (
        'Summarize the sample report\n\nEarlier messages in this thread:\n'
        f'Slack user {view.slack_user_id}: First note\n'
        f'Slack user {view.slack_user_id}: Second note'
    )
    assert 'First note' not in system
    assert 'Second note' not in system
    slack_history_client.conversations_replies.assert_called_once_with(
        channel=view.channel_id,
        ts=view.thread_ts,
        latest=view.message_ts,
        inclusive=False,
        limit=21,
    )


@pytest.mark.asyncio
async def test_missing_history_falls_back_to_verified_mention(
    slack_new_conversation_view, mock_jinja_env, slack_history_client
):
    view = slack_new_conversation_view
    view.thread_ts = '1234567890.000001'
    view.trigger = SlackTrigger(text='Summarize the sample report')
    slack_history_client.conversations_replies.side_effect = SlackApiError(
        'Scope unavailable', {'error': 'missing_scope'}
    )
    with patch(
        'integrations.slack.slack_view.SLACK_CONTEXT_MODE',
        SlackContextMode.REQUESTER_ONLY,
    ):
        prompt, system = await view._get_instructions(mock_jinja_env)

    assert prompt == 'Summarize the sample report'
    assert 'Summarize the sample report' not in system


@pytest.mark.asyncio
async def test_legacy_missing_history_scope_keeps_the_existing_error(
    slack_new_conversation_view, mock_jinja_env, slack_history_client
):
    slack_history_client.conversations_history.side_effect = SlackApiError(
        'Scope unavailable', {'error': 'missing_scope'}
    )
    with (
        patch(
            'integrations.slack.slack_view.SLACK_CONTEXT_MODE',
            SlackContextMode.LEGACY,
        ),
        pytest.raises(SlackError) as error,
    ):
        await slack_new_conversation_view._get_instructions(mock_jinja_env)

    assert error.value.code == SlackErrorCode.MISSING_SLACK_SCOPES


@pytest.mark.asyncio
async def test_exact_thread_lookup_requests_the_reply_instead_of_the_root(
    slack_history_client,
):
    root_ts = '1234567890.000001'
    reply_ts = '1234567890.000002'
    reply = {'ts': reply_ts, 'user': 'UREQUESTER', 'text': 'Review my reply'}
    root = {'ts': root_ts, 'user': 'UREQUESTER', 'text': 'Thread root'}
    slack_history_client.conversations_replies.side_effect = lambda **kwargs: {
        'messages': [reply if kwargs['ts'] == reply_ts else root]
    }

    result = await fetch_exact_message(
        'test-bot-token', 'CCHANNEL', reply_ts, root_ts, 'UREQUESTER'
    )

    assert result == reply
    slack_history_client.conversations_replies.assert_called_once_with(
        channel='CCHANNEL',
        ts=reply_ts,
        oldest=reply_ts,
        latest=reply_ts,
        inclusive=True,
        limit=1,
    )


# Test: V1 Conversation Creation
# ---------------------------------------------------------------------------


class TestV1ConversationCreation:
    """Test V1 conversation creation in Slack integration."""

    @patch.object(SlackNewConversationView, '_create_v1_conversation')
    async def test_v1_conversation_creation(
        self,
        mock_create_v1,
        slack_new_conversation_view,
        mock_jinja_env,
    ):
        """Test that V1 conversations are created correctly."""
        # Setup mocks
        mock_create_v1.return_value = None

        # Execute
        result = await slack_new_conversation_view.create_or_update_conversation(
            mock_jinja_env
        )

        # Verify
        assert result == slack_new_conversation_view.conversation_id
        mock_create_v1.assert_called_once()

    @patch('integrations.slack.slack_view.get_app_conversation_service')
    @patch.object(SlackNewConversationView, '_get_instructions', new_callable=AsyncMock)
    async def test_v1_conversation_start_error_surfaces_task_detail(
        self,
        mock_get_instructions,
        mock_get_app_conversation_service,
        slack_new_conversation_view,
        mock_jinja_env,
    ):
        """A failed start raises the user-facing exception with the task detail."""
        # Arrange
        mock_get_instructions.return_value = ('Hello OpenHands!', '')
        detail = (
            'Unsupported or missing $schema in plugin.json (HTTP 500, error_id=abc123)'
        )
        error_task = AppConversationStartTask(
            created_by_user_id='test-user-123',
            request=AppConversationStartRequest(),
            status=AppConversationStartTaskStatus.ERROR,
            detail=detail,
        )

        async def start_app_conversation(_request):
            yield error_task

        mock_service = MagicMock()
        mock_service.start_app_conversation = start_app_conversation
        mock_get_app_conversation_service.return_value.__aenter__.return_value = (
            mock_service
        )

        # Act
        with pytest.raises(StartingConvoException) as exc_info:
            await slack_new_conversation_view.create_or_update_conversation(
                mock_jinja_env
            )

        # Assert
        assert str(exc_info.value) == f'Failed to start the conversation: {detail}'


# ---------------------------------------------------------------------------
# Test: Message Routing to V1
# ---------------------------------------------------------------------------


class TestMessageRouting:
    """Test that message sending routes to V1 method."""

    @patch.object(
        SlackUpdateExistingConversationView, 'send_message_to_v1_conversation'
    )
    async def test_message_routing_to_v1(
        self,
        mock_send_v1,
        slack_update_conversation_view_v1,
        mock_jinja_env,
    ):
        """Test that V1 conversations route to V1 message sending method."""
        # Setup
        mock_send_v1.return_value = None

        # Execute
        result = await slack_update_conversation_view_v1.create_or_update_conversation(
            mock_jinja_env
        )

        # Verify
        assert result == slack_update_conversation_view_v1.conversation_id
        mock_send_v1.assert_called_once_with(mock_jinja_env)


# ---------------------------------------------------------------------------
# Test: Paused Sandbox Resumption for V1 Conversations
# ---------------------------------------------------------------------------


class TestPausedSandboxResumption:
    """Test that paused sandboxes are resumed when sending messages to V1 conversations."""

    @patch('openhands.app_server.config.get_sandbox_service')
    @patch('openhands.app_server.config.get_app_conversation_info_service')
    @patch('openhands.app_server.config.get_httpx_client')
    @patch('openhands.app_server.event_callback.util.ensure_running_sandbox')
    @patch('openhands.app_server.event_callback.util.get_agent_server_url_from_sandbox')
    @patch.object(
        SlackUpdateExistingConversationView, '_get_instructions', new_callable=AsyncMock
    )
    async def test_paused_sandbox_resumption(
        self,
        mock_get_instructions,
        mock_get_agent_server_url,
        mock_ensure_running_sandbox,
        mock_get_httpx_client,
        mock_get_app_info_service,
        mock_get_sandbox_service,
        slack_update_conversation_view_v1,
        mock_jinja_env,
    ):
        """Test that paused sandboxes are resumed when sending messages to V1 conversations."""
        # Setup mocks
        mock_get_instructions.return_value = ('User message', '')

        # Mock app conversation info service
        mock_app_info_service = AsyncMock()
        mock_app_info = MagicMock()
        mock_app_info.sandbox_id = 'sandbox-123'
        mock_app_info_service.get_app_conversation_info.return_value = mock_app_info
        mock_get_app_info_service.return_value.__aenter__.return_value = (
            mock_app_info_service
        )

        # Mock sandbox service with paused sandbox that gets resumed
        mock_sandbox_service = AsyncMock()
        mock_paused_sandbox = MagicMock()
        mock_paused_sandbox.status = SandboxStatus.PAUSED
        mock_paused_sandbox.session_api_key = 'test-api-key'
        mock_paused_sandbox.exposed_urls = [
            MagicMock(name='AGENT_SERVER', url='http://localhost:8000')
        ]

        # After resume, sandbox becomes running
        mock_running_sandbox = MagicMock()
        mock_running_sandbox.status = SandboxStatus.RUNNING
        mock_running_sandbox.session_api_key = 'test-api-key'
        mock_running_sandbox.exposed_urls = [
            MagicMock(name='AGENT_SERVER', url='http://localhost:8000')
        ]

        mock_sandbox_service.get_sandbox.side_effect = [
            mock_paused_sandbox,
            mock_running_sandbox,
        ]
        mock_sandbox_service.resume_sandbox = AsyncMock()
        mock_get_sandbox_service.return_value.__aenter__.return_value = (
            mock_sandbox_service
        )

        # Mock ensure_running_sandbox to first raise RuntimeError, then return running sandbox
        mock_ensure_running_sandbox.side_effect = [
            RuntimeError('Sandbox not running: sandbox-123'),
            mock_running_sandbox,
        ]

        # Mock agent server URL
        mock_get_agent_server_url.return_value = 'http://localhost:8000'

        # Mock HTTP client
        mock_httpx_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response
        mock_get_httpx_client.return_value.__aenter__.return_value = mock_httpx_client

        # Execute
        await slack_update_conversation_view_v1.send_message_to_v1_conversation(
            mock_jinja_env
        )

        # Verify sandbox was resumed
        mock_sandbox_service.resume_sandbox.assert_called_once_with('sandbox-123')
        mock_httpx_client.post.assert_called_once()
        mock_response.raise_for_status.assert_called_once()


# ---------------------------------------------------------------------------
# Test: Agent server errors on follow-up messages
# ---------------------------------------------------------------------------


class TestV1SendMessageFailure:
    """Test that agent server error responses reach the user on follow-ups."""

    @patch('openhands.app_server.config.get_sandbox_service')
    @patch('openhands.app_server.config.get_app_conversation_info_service')
    @patch('openhands.app_server.config.get_httpx_client')
    @patch('openhands.app_server.event_callback.util.get_agent_server_url_from_sandbox')
    @patch.object(
        SlackUpdateExistingConversationView, '_get_instructions', new_callable=AsyncMock
    )
    async def test_agent_server_error_response_surfaces_its_message(
        self,
        mock_get_instructions,
        mock_get_agent_server_url,
        mock_get_httpx_client,
        mock_get_app_info_service,
        mock_get_sandbox_service,
        slack_update_conversation_view_v1,
        mock_jinja_env,
    ):
        """An HTTP error from the agent server surfaces the response body."""
        # Arrange
        mock_get_instructions.return_value = ('Follow up message', '')
        mock_get_agent_server_url.return_value = 'http://localhost:8000'

        mock_app_info_service = AsyncMock()
        mock_app_info_service.get_app_conversation_info.return_value = MagicMock(
            sandbox_id='sandbox-123'
        )
        mock_get_app_info_service.return_value.__aenter__.return_value = (
            mock_app_info_service
        )

        mock_running_sandbox = MagicMock(
            status=SandboxStatus.RUNNING, session_api_key='test-api-key'
        )
        mock_sandbox_service = AsyncMock()
        mock_sandbox_service.get_sandbox.return_value = mock_running_sandbox
        mock_sandbox_service.wait_for_sandbox_running.return_value = (
            mock_running_sandbox
        )
        mock_get_sandbox_service.return_value.__aenter__.return_value = (
            mock_sandbox_service
        )

        request = httpx.Request('POST', 'http://localhost:8000/api/conversations')
        error_response = httpx.Response(
            500,
            request=request,
            json={
                'detail': 'Internal Server Error',
                'exception': 'Unsupported or missing $schema in plugin.json',
                'error_id': 'abc123',
            },
        )
        mock_httpx_client = AsyncMock()
        mock_httpx_client.post.return_value = error_response
        mock_get_httpx_client.return_value.__aenter__.return_value = mock_httpx_client

        # Act
        with pytest.raises(StartingConvoException) as exc_info:
            await slack_update_conversation_view_v1.send_message_to_v1_conversation(
                mock_jinja_env
            )

        # Assert
        assert str(exc_info.value) == (
            'Failed to send your message to the conversation: '
            'Unsupported or missing $schema in plugin.json (HTTP 500, error_id=abc123)'
        )
