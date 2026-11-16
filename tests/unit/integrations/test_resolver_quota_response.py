"""Resolver integrations reply with the usage-limit message on quota failures.

GitHub has its own end-to-end test in github/test_github_quota_response.py.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from integrations.azure_devops.azure_devops_manager import AzureDevOpsManager
from integrations.azure_devops.azure_devops_view import AzureDevOpsItem
from integrations.bitbucket.bitbucket_manager import BitbucketManager
from integrations.bitbucket.bitbucket_view import BitbucketPRComment
from integrations.bitbucket_data_center.bitbucket_dc_manager import BitbucketDCManager
from integrations.bitbucket_data_center.bitbucket_dc_view import BitbucketDCPRComment
from integrations.gitlab.gitlab_manager import GitlabManager
from integrations.gitlab.gitlab_view import GitlabIssue
from integrations.jira.jira_manager import JiraManager
from integrations.jira.jira_view import JiraNewConversationView
from integrations.jira_dc.jira_dc_manager import JiraDcManager
from integrations.jira_dc.jira_dc_view import JiraDcNewConversationView
from integrations.slack.slack_manager import SlackManager
from integrations.slack.slack_types import StartingConvoException
from integrations.slack.slack_view import SlackNewConversationView
from integrations.types import UserData
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartRequest,
    AppConversationStartTask,
    AppConversationStartTaskStatus,
)
from server.services.daily_sandbox_quota_service import (
    QUOTA_INCREASE_REQUEST_URL,
    DailySandboxLimitReached,
    DailySandboxQuotaService,
)

HOST = 'https://app.example.com'
QUOTA_LINK = f'[quota settings]({HOST}/settings/quota)'
FORM_LINK = f'[quota increase request form]({QUOTA_INCREASE_REQUEST_URL})'


def _quota_error() -> DailySandboxLimitReached:
    return DailySandboxQuotaService._limit_reached(20, 20, date(2026, 11, 9))


def _git_user() -> UserData:
    return UserData(user_id='user-1', username='alice', keycloak_user_id='kc-alice')


# --- Managers: what each integration posts back ------------------------------


async def _git_provider_reply(manager_cls, module: str, error: Exception) -> str:
    manager = manager_cls(AsyncMock())
    view = MagicMock()
    view.user_info = _git_user()
    view.initialize_new_conversation = AsyncMock(return_value=uuid4())
    view.create_new_conversation = AsyncMock(side_effect=error)
    with (
        patch(f'{module}.get_saas_user_auth', new=AsyncMock()),
        patch.object(manager, 'send_message', new=AsyncMock()) as send,
    ):
        await manager.start_job(view)
    send.assert_awaited_once()
    return send.await_args.args[0]


async def _gitlab_reply(error):
    return await _git_provider_reply(
        GitlabManager, 'integrations.gitlab.gitlab_manager', error
    )


async def _bitbucket_reply(error):
    return await _git_provider_reply(
        BitbucketManager, 'integrations.bitbucket.bitbucket_manager', error
    )


async def _bitbucket_dc_reply(error):
    return await _git_provider_reply(
        BitbucketDCManager,
        'integrations.bitbucket_data_center.bitbucket_dc_manager',
        error,
    )


async def _azure_devops_reply(error):
    return await _git_provider_reply(
        AzureDevOpsManager, 'integrations.azure_devops.azure_devops_manager', error
    )


async def _jira_reply(error):
    with patch('integrations.jira.jira_manager.JiraIntegrationStore.get_instance'):
        manager = JiraManager(MagicMock())
    view = MagicMock()
    view.create_or_update_conversation = AsyncMock(side_effect=error)
    manager._send_comment = AsyncMock()
    await manager.start_job(view)
    manager._send_comment.assert_awaited_once()
    return manager._send_comment.await_args.args[1]


async def _jira_dc_reply(error):
    with patch(
        'integrations.jira_dc.jira_dc_manager.JiraDcIntegrationStore.get_instance'
    ):
        manager = JiraDcManager(MagicMock())
    view = MagicMock()
    view.create_or_update_conversation = AsyncMock(side_effect=error)
    manager.send_message = AsyncMock()
    with patch('integrations.jira_dc.jira_dc_manager.resolve_jira_dc_service_account'):
        await manager.start_job(view)
    manager.send_message.assert_awaited_once()
    return manager.send_message.await_args.args[0]


async def _slack_reply(error):
    manager = SlackManager(MagicMock())
    view = MagicMock()
    view.slack_to_openhands_user.slack_display_name = 'alice'
    view.create_or_update_conversation = AsyncMock(side_effect=error)
    manager.send_message = AsyncMock()
    await manager.start_job(view)
    manager.send_message.assert_awaited_once()
    return manager.send_message.await_args.args[0]


GIT_REPLIES = [
    _gitlab_reply,
    _bitbucket_reply,
    _bitbucket_dc_reply,
    _azure_devops_reply,
]
JIRA_REPLIES = [_jira_reply, _jira_dc_reply]
ALL_REPLIES = [*GIT_REPLIES, *JIRA_REPLIES, _slack_reply]


@pytest.fixture(autouse=True)
def _host_url():
    with patch('integrations.utils.HOST_URL', HOST):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize('reply', ALL_REPLIES, ids=lambda f: f.__name__)
async def test_quota_failure_posts_usage_limit_message(reply):
    message = await reply(_quota_error())

    assert 'Usage limit reached.' in message
    assert QUOTA_LINK in message
    assert FORM_LINK in message
    assert '20' not in message.replace(QUOTA_INCREASE_REQUEST_URL, '')
    assert 'unexpected error' not in message
    assert 'try again' not in message.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'reply', [*GIT_REPLIES, _slack_reply], ids=lambda f: f.__name__
)
async def test_quota_reply_mentions_the_user(reply):
    message = await reply(_quota_error())
    assert message.startswith('@alice there was an error starting the job: ')


@pytest.mark.asyncio
@pytest.mark.parametrize('reply', JIRA_REPLIES, ids=lambda f: f.__name__)
async def test_jira_quota_reply_has_no_mention(reply):
    message = await reply(_quota_error())
    assert message.startswith('There was an error starting the job: ')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'reply,expected',
    [
        *(
            (r, 'Uh oh! There was an unexpected error starting the job :(')
            for r in GIT_REPLIES
        ),
        *(
            (
                r,
                'Sorry, there was an unexpected error starting the job. Please try again.',
            )
            for r in JIRA_REPLIES
        ),
        (_slack_reply, 'Uh oh! There was an unexpected error starting the job :('),
    ],
    ids=lambda v: getattr(v, '__name__', ''),
)
async def test_unrelated_failure_keeps_existing_reply(reply, expected):
    assert await reply(RuntimeError('private diagnostic')) == expected


@pytest.mark.asyncio
async def test_slack_start_error_detail_still_reaches_user():
    message = await _slack_reply(
        StartingConvoException('Failed to start the conversation: bad plugin')
    )
    assert message == 'Failed to start the conversation: bad plugin'


# --- Views: the typed quota error is not wrapped -----------------------------


def _service_yielding(error: Exception | None):
    async def start(_request):
        task = AppConversationStartTask(
            created_by_user_id='kc-alice',
            request=AppConversationStartRequest(),
            status=AppConversationStartTaskStatus.ERROR,
            detail='private diagnostic',
        )
        task._startup_error = error
        yield task

    service = MagicMock(start_app_conversation=start)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=service)
    context.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=context)


def _stub_view(**extra):
    return SimpleNamespace(
        user_info=_git_user(),
        issue_number=7,
        title='Quota regression',
        full_repo_name='ws/repo',
        workspace='ws',
        repo_slug='repo',
        project_key='PROJ',
        installation_id='inst',
        send_summary_instruction=False,
        resolved_org_id=None,
        is_mr=False,
        _get_instructions=AsyncMock(return_value=('do it', 'context')),
        _get_branch_name=lambda: 'main',
        **extra,
    )


async def _start_gitlab(module):
    view = _stub_view(_create_gitlab_v1_callback_processor=MagicMock())
    await GitlabIssue._create_v1_conversation(view, MagicMock(), MagicMock(), uuid4())


async def _start_bitbucket(module):
    with patch(
        'integrations.bitbucket.bitbucket_v1_callback_processor.BitbucketV1CallbackProcessor'
    ):
        await BitbucketPRComment.create_new_conversation(
            _stub_view(), MagicMock(), {}, uuid4(), MagicMock()
        )


async def _start_bitbucket_dc(module):
    with patch(
        'integrations.bitbucket_data_center.bitbucket_dc_v1_callback_processor.BitbucketDCV1CallbackProcessor'
    ):
        await BitbucketDCPRComment.create_new_conversation(
            _stub_view(), MagicMock(), {}, uuid4(), MagicMock()
        )


async def _start_azure_devops(module):
    with patch(
        'integrations.azure_devops.azure_devops_v1_callback_processor.AzureDevOpsV1CallbackProcessor'
    ):
        await AzureDevOpsItem.create_new_conversation(
            _stub_view(), MagicMock(), {}, uuid4(), MagicMock()
        )


async def _start_jira(module):
    view = SimpleNamespace(
        saas_user_auth=MagicMock(),
        resolved_org_id=None,
        selected_repo='ws/repo',
        git_provider=None,
        additional_repos=[],
        payload=SimpleNamespace(issue_key='PROJ-1'),
        _issue_title='Quota regression',
        _get_v1_initial_user_message=AsyncMock(return_value='do it'),
        _create_jira_v1_callback_processor=MagicMock(),
    )
    await JiraNewConversationView._create_v1_conversation(view, MagicMock(), uuid4())


async def _start_jira_dc(module):
    view = SimpleNamespace(
        saas_user_auth=MagicMock(),
        selected_repo='ws/repo',
        additional_repos=[],
        resolved_org_id=None,
        conversation_id=uuid4().hex,
        job_context=SimpleNamespace(issue_key='PROJ-1', issue_title='Quota regression'),
        _get_instructions=AsyncMock(return_value=('context', 'do it')),
        _create_jira_dc_v1_callback_processor=MagicMock(),
        _get_resolved_org_id=AsyncMock(return_value=None),
        _get_git_provider=AsyncMock(return_value=None),
    )
    await JiraDcNewConversationView._create_v1_conversation(view, MagicMock())


async def _start_slack(module):
    view = MagicMock()
    view._get_instructions = AsyncMock(return_value=('do it', 'context'))
    await SlackNewConversationView._create_v1_conversation(view, MagicMock())


VIEW_STARTS = [
    (_start_gitlab, 'integrations.gitlab.gitlab_view', RuntimeError),
    (_start_bitbucket, 'integrations.bitbucket.bitbucket_view', RuntimeError),
    (
        _start_bitbucket_dc,
        'integrations.bitbucket_data_center.bitbucket_dc_view',
        RuntimeError,
    ),
    (_start_azure_devops, 'integrations.azure_devops.azure_devops_view', RuntimeError),
    (_start_jira, 'integrations.jira.jira_view', RuntimeError),
    (_start_jira_dc, 'integrations.jira_dc.jira_dc_view', RuntimeError),
    (_start_slack, 'integrations.slack.slack_view', StartingConvoException),
]


async def _run_view_start(start, module, error):
    with (
        patch(f'{module}.get_app_conversation_service', _service_yielding(error)),
        patch(f'{module}.AppConversationStartRequest'),
        patch(f'{module}.ResolverUserContext', create=True),
    ):
        await start(module)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'start,module,_generic', VIEW_STARTS, ids=[m for _, m, _ in VIEW_STARTS]
)
async def test_view_reraises_quota_error(start, module, _generic):
    error = _quota_error()
    with pytest.raises(DailySandboxLimitReached) as raised:
        await _run_view_start(start, module, error)
    assert raised.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'start,module,generic', VIEW_STARTS, ids=[m for _, m, _ in VIEW_STARTS]
)
async def test_view_wraps_other_start_errors(start, module, generic):
    with pytest.raises(generic):
        await _run_view_start(start, module, RuntimeError('private diagnostic'))
