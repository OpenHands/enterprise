"""Tests for save_pr_metadata: the PR reference stored after an MCP tool opens a PR."""

import pytest

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
    PullRequestRef,
)
from openhands.app_server.config import get_app_conversation_info_service
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.mcp.mcp_router import save_pr_metadata
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.specifiy_user_context import (
    USER_CONTEXT_ATTR,
    SpecifyUserContext,
)


def _admin_state() -> InjectorState:
    state = InjectorState()
    setattr(state, USER_CONTEXT_ATTR, SpecifyUserContext(None))
    return state


async def _save(
    tool_result: str,
    git_provider: ProviderType,
    repository: str,
    web_base_url: str | None = None,
):
    """Save a conversation, run save_pr_metadata on it, and reload it from the DB."""
    info = AppConversationInfo(created_by_user_id=None, sandbox_id='sandbox-1')
    async with get_app_conversation_info_service(_admin_state()) as service:
        await service.save_app_conversation_info(info)

    await save_pr_metadata(
        None,
        str(info.id),
        tool_result,
        git_provider=git_provider,
        repository=repository,
        web_base_url=web_base_url,
    )

    async with get_app_conversation_info_service(_admin_state()) as service:
        saved = await service.get_app_conversation_info(info.id)
    assert saved is not None
    return saved


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('tool_result', 'git_provider', 'repository', 'expected'),
    [
        # GitHub: the canonical owner/repo comes from the URL, not the agent input.
        (
            'https://github.com/OpenHands/OpenHands/pull/42',
            ProviderType.GITHUB,
            'openhands/openhands',
            PullRequestRef(
                number=42,
                repository='OpenHands/OpenHands',
                git_provider=ProviderType.GITHUB,
                url='https://github.com/OpenHands/OpenHands/pull/42',
            ),
        ),
        # GitLab with a numeric project ID: the path comes from the URL.
        (
            'https://gitlab.com/group/sub/repo/-/merge_requests/7',
            ProviderType.GITLAB,
            '12345',
            PullRequestRef(
                number=7,
                repository='group/sub/repo',
                git_provider=ProviderType.GITLAB,
                url='https://gitlab.com/group/sub/repo/-/merge_requests/7',
            ),
        ),
        # GitLab with a URL-encoded project path.
        (
            'https://gitlab.example.com/group/sub/repo/-/merge_requests/8',
            ProviderType.GITLAB,
            'group%2Fsub%2Frepo',
            PullRequestRef(
                number=8,
                repository='group/sub/repo',
                git_provider=ProviderType.GITLAB,
                url='https://gitlab.example.com/group/sub/repo/-/merge_requests/8',
            ),
        ),
        # Azure DevOps: the repository is the tool input.
        (
            'https://dev.azure.com/org/proj/_git/repo/pullrequest/9',
            ProviderType.AZURE_DEVOPS,
            'org/proj/repo',
            PullRequestRef(
                number=9,
                repository='org/proj/repo',
                git_provider=ProviderType.AZURE_DEVOPS,
                url='https://dev.azure.com/org/proj/_git/repo/pullrequest/9',
            ),
        ),
        # A result that is not an http(s) URL is never stored as a link.
        (
            'javascript:alert(1)//pull/5',
            ProviderType.GITHUB,
            'acme/widgets',
            PullRequestRef(
                number=5,
                repository='acme/widgets',
                git_provider=ProviderType.GITHUB,
                url=None,
            ),
        ),
    ],
)
async def test_save_pr_metadata_stores_pull_request_ref(
    app_db_session, tool_result, git_provider, repository, expected
):
    info = await _save(tool_result, git_provider, repository)

    assert info.pr_number == [expected.number]
    assert info.pull_requests == [expected]


@pytest.mark.asyncio
async def test_save_pr_metadata_strips_gitlab_instance_sub_path(app_db_session):
    url = 'https://host.example/gitlab/group/repo/-/merge_requests/7'
    info = await _save(
        url,
        ProviderType.GITLAB,
        '42',
        web_base_url='https://host.example/gitlab',
    )

    assert info.pull_requests == [
        PullRequestRef(
            number=7,
            repository='group/repo',
            git_provider=ProviderType.GITLAB,
            url=url,
        )
    ]


@pytest.mark.asyncio
async def test_save_pr_metadata_without_pr_number_stores_nothing(app_db_session):
    info = await _save('', ProviderType.BITBUCKET, 'workspace/repo')

    assert info.pr_number == []
    assert info.pull_requests == []
