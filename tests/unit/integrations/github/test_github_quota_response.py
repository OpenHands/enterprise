from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from integrations.github.github_manager import GithubManager
from integrations.github.github_view import GithubIssue
from integrations.models import Message, SourceType
from integrations.types import UserData
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartRequest,
    AppConversationStartTask,
    AppConversationStartTaskStatus,
)
from server.services.daily_sandbox_quota_service import DailySandboxQuotaService


@pytest.mark.asyncio
@pytest.mark.parametrize('limit,used', [(20, 20), (50, 65), (0, 0)])
async def test_quota_failure_reaches_github_comment(limit, used):
    error = DailySandboxQuotaService._limit_reached(limit, used, date(2026, 10, 22))
    message = await _run_job(error)

    assert f'**{used} used / {limit} limit**' in message
    assert '**2026-10-23 00:00 UTC**' in message
    assert 'https://app.example.com/settings/quota' in message
    assert 'shared across your PRs' in message
    assert 'retry after the reset' in message
    assert 'Uh oh' not in message
    assert 'error starting the job' in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error',
    [
        RuntimeError('private diagnostic'),
        HTTPException(429, {'message': 'private diagnostic'}),
        RuntimeError('Daily sandbox limit of 20 reached.'),
    ],
)
async def test_unrelated_failure_stays_generic(error):
    message = await _run_job(error)
    assert message == 'Uh oh! There was an unexpected error starting the job :('


@pytest.mark.asyncio
async def test_success_still_posts_conversation_link():
    message = await _run_job(None)
    assert message.startswith("I'm on it! testuser can")
    assert 'quota' not in message


async def _run_job(error):
    user = UserData(user_id=123, username='testuser', keycloak_user_id=str(uuid4()))
    conversation_id = uuid4()
    view = GithubIssue(
        issue_number=42,
        installation_id=123,
        full_repo_name='example/repo',
        is_public_repo=False,
        user_info=user,
        raw_payload=Message(source=SourceType.GITHUB, message={}),
        conversation_id=conversation_id.hex,
        uuid=None,
        should_extract=False,
        send_summary_instruction=False,
        title='Quota regression',
        description='',
        previous_comments=[],
    )
    view.initialize_new_conversation = AsyncMock(return_value=conversation_id)
    view._get_v1_initial_user_message = AsyncMock(return_value='Review this PR')
    view.resolved_org_id = None
    token_manager = MagicMock()
    token_manager.get_idp_token_from_idp_user_id = AsyncMock(return_value='test-token')
    collector = MagicMock(save_data=AsyncMock())

    async def start(request):
        task = AppConversationStartTask(
            created_by_user_id=user.keycloak_user_id,
            request=AppConversationStartRequest(),
            status=(
                AppConversationStartTaskStatus.ERROR
                if error
                else AppConversationStartTaskStatus.READY
            ),
            detail='private diagnostic' if error else None,
        )
        task._startup_error = error
        yield task

    service = MagicMock(start_app_conversation=start)
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(return_value=service)
    context_manager.__aexit__ = AsyncMock(return_value=False)
    with (
        patch('integrations.github.github_manager.GithubIntegration'),
        patch('integrations.github.github_manager.Auth'),
        patch('integrations.github.github_manager.get_saas_user_auth', new=AsyncMock()),
        patch('integrations.github.github_manager.HOST_URL', 'https://app.example.com'),
        patch(
            'integrations.github.github_view.get_app_conversation_service',
            return_value=context_manager,
        ),
    ):
        manager = GithubManager(token_manager, collector)
        manager.send_message = AsyncMock()
        await manager.start_job(view)

    manager.send_message.assert_awaited_once()
    collector.save_data.assert_awaited_once_with(view)
    return manager.send_message.await_args.args[0]
