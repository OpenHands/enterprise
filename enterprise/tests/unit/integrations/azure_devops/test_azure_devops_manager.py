"""Tests for AzureDevOpsManager.receive_message write-access gating."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from integrations.azure_devops.azure_devops_manager import AzureDevOpsManager
from integrations.azure_devops.azure_devops_view import AzureDevOpsPRComment
from integrations.models import Message, SourceType
from integrations.types import UserData


def _pr_comment_message(body: str = '@openhands please fix this') -> Message:
    return Message(
        source=SourceType.AZURE_DEVOPS,
        message={
            'event_key': 'ms.vss-code.git-pullrequest-comment-event',
            'payload': {
                'eventType': 'ms.vss-code.git-pullrequest-comment-event',
                'resourceContainers': {
                    'account': {'baseUrl': 'https://dev.azure.com/alonaking/'}
                },
                'resource': {
                    'comment': {
                        'id': 2,
                        'author': {
                            'id': 'ado-user-id',
                            'displayName': 'Alice Example',
                            'uniqueName': 'alice@example.com',
                        },
                        'content': body,
                    },
                    'pullRequest': {
                        'pullRequestId': 7,
                        'sourceRefName': 'refs/heads/feature/x',
                        'repository': {
                            'id': 'repo-1',
                            'name': 'Repo',
                            'project': {'id': 'proj-1', 'name': 'Project'},
                        },
                    },
                },
            },
        },
    )


@pytest.mark.asyncio
async def test_receive_message_skips_when_commenter_lacks_write_access(monkeypatch):
    manager = AzureDevOpsManager(AsyncMock())
    manager._resolve_mentioner_keycloak_id = AsyncMock(return_value='kc-alice')  # type: ignore[method-assign]
    manager.start_job = AsyncMock()  # type: ignore[method-assign]

    fake_service = MagicMock()
    fake_service.has_contribute_access = AsyncMock(return_value=False)
    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_manager.AzureDevOpsServiceImpl',
        lambda external_auth_id: fake_service,
    )

    await manager.receive_message(_pr_comment_message())

    fake_service.has_contribute_access.assert_awaited_once_with('proj-1', 'repo-1')
    manager.start_job.assert_not_called()


@pytest.mark.asyncio
async def test_receive_message_dispatches_when_commenter_has_write_access(monkeypatch):
    manager = AzureDevOpsManager(AsyncMock())
    manager._resolve_mentioner_keycloak_id = AsyncMock(return_value='kc-alice')  # type: ignore[method-assign]
    manager.start_job = AsyncMock()  # type: ignore[method-assign]

    fake_service = MagicMock()
    fake_service.has_contribute_access = AsyncMock(return_value=True)
    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_manager.AzureDevOpsServiceImpl',
        lambda external_auth_id: fake_service,
    )

    await manager.receive_message(_pr_comment_message())

    fake_service.has_contribute_access.assert_awaited_once_with('proj-1', 'repo-1')
    manager.start_job.assert_awaited_once()


def _pr_comment_view(thread_id: int | None = 11) -> AzureDevOpsPRComment:
    return AzureDevOpsPRComment(
        installation_id='org/Project/Repo',
        issue_number=7,
        full_repo_name='org/Project/Repo',
        is_public_repo=False,
        user_info=UserData(
            user_id='ado-user-id', username='alice', keycloak_user_id='kc-alice'
        ),
        raw_payload=_pr_comment_message(),
        conversation_id='',
        should_extract=True,
        send_summary_instruction=True,
        title='',
        description='',
        previous_comments=[],
        project_name='Project',
        project_id='proj-1',
        repository_id='repo-1',
        comment_id=2,
        comment_body='@openhands please fix this',
        thread_id=thread_id,
        branch_name='feature/x',
    )


@pytest.mark.asyncio
async def test_send_message_posts_as_bot_when_token_set(monkeypatch):
    """With a bot PAT set, replies post via the bot service, not the mentioner."""
    manager = AzureDevOpsManager(AsyncMock())
    bot_service = MagicMock()
    bot_service.add_pr_comment_to_thread = AsyncMock()

    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_bot_credential.AZURE_DEVOPS_BOT_TOKEN',
        'bot-pat-123',
    )
    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_bot_credential.SaaSAzureDevOpsService',
        lambda: bot_service,
    )
    mentioner_cls = MagicMock()
    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_manager.AzureDevOpsServiceImpl',
        mentioner_cls,
    )

    await manager.send_message('Done', _pr_comment_view(thread_id=11))

    bot_service.add_pr_comment_to_thread.assert_awaited_once()
    assert bot_service.token.get_secret_value() == 'bot-pat-123'
    mentioner_cls.assert_not_called()


@pytest.mark.asyncio
async def test_send_message_posts_as_mentioner_when_token_unset(monkeypatch):
    """With no bot PAT, replies post as the mentioner (backward compatible)."""
    manager = AzureDevOpsManager(AsyncMock())
    mentioner_service = MagicMock()
    mentioner_service.add_pr_comment_to_thread = AsyncMock()
    mentioner_cls = MagicMock(return_value=mentioner_service)

    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_bot_credential.AZURE_DEVOPS_BOT_TOKEN',
        '',
    )
    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_manager.AzureDevOpsServiceImpl',
        mentioner_cls,
    )

    await manager.send_message('Done', _pr_comment_view(thread_id=11))

    mentioner_cls.assert_called_once_with(external_auth_id='kc-alice')
    mentioner_service.add_pr_comment_to_thread.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'field,value',
    [
        ('uniqueName', 'openhands-bot'),
        ('displayName', 'OpenHands-Bot'),  # case-insensitive match
        ('id', 'openhands-bot'),
    ],
)
async def test_receive_message_skips_event_authored_by_bot(monkeypatch, field, value):
    """Events authored by the bot account never start a job."""
    monkeypatch.setattr(
        'integrations.azure_devops.azure_devops_manager.AZURE_DEVOPS_BOT_USERNAME',
        'openhands-bot',
    )
    manager = AzureDevOpsManager(AsyncMock())
    manager._resolve_mentioner_keycloak_id = AsyncMock(return_value='kc-alice')  # type: ignore[method-assign]
    manager.start_job = AsyncMock()  # type: ignore[method-assign]

    message = _pr_comment_message()
    message.message['payload']['resource']['comment']['author'] = {field: value}

    await manager.receive_message(message)

    manager.start_job.assert_not_called()
    manager._resolve_mentioner_keycloak_id.assert_not_awaited()
