"""The git-provider resolvers clone repositories named in the triggering comment
beside the webhook's repository."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from integrations.azure_devops.azure_devops_view import (
    AzureDevOpsPRComment,
    AzureDevOpsWorkItemComment,
)
from integrations.bitbucket.bitbucket_view import BitbucketPRComment
from integrations.bitbucket_data_center.bitbucket_dc_view import BitbucketDCPRComment
from integrations.github.github_view import GithubIssue, GithubIssueComment
from integrations.gitlab.gitlab_view import GitlabIssueComment
from integrations.mentioned_repositories import MentionedRepositories
from integrations.types import UserData
from openhands.app_server.integrations.service_types import ProviderType, Repository

USER = UserData(user_id=1, username='alice', keycloak_user_id='kc-alice')
COMMON = dict(
    conversation_id='',
    should_extract=True,
    send_summary_instruction=True,
    installation_id='inst',
    user_info=USER,
    issue_number=7,
    is_public_repo=False,
    raw_payload=MagicMock(),
    title='Title',
    description='',
    previous_comments=[],
)


def _github(comment, primary='acme/web'):
    return GithubIssueComment(
        **{**COMMON, 'installation_id': 1},
        full_repo_name=primary,
        uuid=None,
        comment_body=comment,
        comment_id=1,
    )


def _gitlab(comment, primary='acme/web'):
    return GitlabIssueComment(
        **COMMON,
        full_repo_name=primary,
        project_id=3,
        is_mr=False,
        comment_body=comment,
        discussion_id='d',
        confidential=False,
    )


def _bitbucket(comment, primary='acme/web'):
    return BitbucketPRComment(
        **COMMON,
        full_repo_name=primary,
        workspace='acme',
        repo_slug='web',
        branch_name='feature',
        comment_body=comment,
        parent_comment_id=None,
    )


def _bitbucket_dc(comment, primary='DEM/web'):
    return BitbucketDCPRComment(
        **COMMON,
        full_repo_name=primary,
        project_key='DEM',
        repo_slug='web',
        branch_name='feature',
        installer_keycloak_user_id='kc-installer',
        comment_id=1,
        comment_body=comment,
        parent_comment_id=None,
    )


def _azure(comment, primary='org/proj/web'):
    return AzureDevOpsPRComment(
        **COMMON,
        full_repo_name=primary,
        project_name='proj',
        project_id='p1',
        repository_id='r1',
        comment_id=1,
        comment_body=comment,
        thread_id=5,
        branch_name='feature',
    )


RESOLVERS = [
    # (view factory, view module, provider, primary, other repo)
    (
        _github,
        'integrations.github.github_view',
        ProviderType.GITHUB,
        'acme/web',
        'acme/api',
    ),
    (
        _gitlab,
        'integrations.gitlab.gitlab_view',
        ProviderType.GITLAB,
        'acme/web',
        'acme/api',
    ),
    (
        _bitbucket,
        'integrations.bitbucket.bitbucket_view',
        ProviderType.BITBUCKET,
        'acme/web',
        'acme/api',
    ),
    (
        _bitbucket_dc,
        'integrations.bitbucket_data_center.bitbucket_dc_view',
        ProviderType.BITBUCKET_DATA_CENTER,
        'DEM/web',
        'DEM/api',
    ),
    (
        _azure,
        'integrations.azure_devops.azure_devops_view',
        ProviderType.AZURE_DEVOPS,
        'org/proj/web',
        'org/proj/api',
    ),
]
RESOLVER_IDS = ['github', 'gitlab', 'bitbucket', 'bitbucket_dc', 'azure_devops']


def _mention(name: str) -> str:
    """Azure DevOps repositories are named by URL; others by owner/repo."""
    org, *rest = name.split('/')
    if len(rest) == 2:
        return f'https://dev.azure.com/{org}/{rest[0]}/_git/{rest[1]}'
    return name


def _jinja_env():
    env = MagicMock()
    env.get_template.return_value.render.return_value = 'rendered'
    return env


def _handler(accessible: dict[str, Repository]):
    async def _verify(name, *args, **kwargs):
        if name.lower() not in accessible:
            raise Exception(f'404 {name}')
        return accessible[name.lower()]

    handler = MagicMock()
    handler.verify_repo_provider = AsyncMock(side_effect=_verify)
    return handler


def _repo(name: str, provider: ProviderType) -> Repository:
    return Repository(id=name, full_name=name, git_provider=provider, is_public=False)


async def _start(view, module, handler):
    """Run the resolver's create path; return the start request and handler factory."""
    view.resolved_org_id = None
    view._load_resolver_context = AsyncMock()
    captured = []

    class _Service:
        async def start_app_conversation(self, request):
            captured.append(request)
            if False:
                yield

    @asynccontextmanager
    async def _service(_state):
        yield _Service()

    create_handler = AsyncMock(return_value=handler)
    with (
        patch(f'{module}.get_app_conversation_service', _service),
        patch(
            'integrations.mentioned_repositories.create_provider_handler',
            create_handler,
        ),
    ):
        await view.create_new_conversation(_jinja_env(), {}, uuid4(), MagicMock())
    assert len(captured) == 1
    return captured[0], create_handler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'make,module,provider,primary,other', RESOLVERS, ids=RESOLVER_IDS
)
async def test_mentioned_repository_is_cloned_beside_webhook_repository(
    make, module, provider, primary, other
):
    view = make(f'@openhands also update {_mention(other)} please')
    # The description may be written by someone else: it never adds repositories.
    view.description = (
        f'Context: {primary}, acme/upstream and https://docs.example.com/guide/intro'
    )
    handler = _handler(
        {
            other.lower(): _repo(other, provider),
            'acme/upstream': _repo('acme/upstream', provider),
        }
    )

    request, _ = await _start(view, module, handler)

    assert request.selected_repository == primary
    assert request.git_provider == provider
    assert [
        (r.repository, r.git_provider) for r in request.additional_repositories
    ] == [(other, provider)]
    # The webhook's repository is never looked up again; the webhook's provider
    # is tried first.
    looked_up = [c.args[0] for c in handler.verify_repo_provider.await_args_list]
    assert primary not in looked_up
    assert all(
        c.args[1] == provider for c in handler.verify_repo_provider.await_args_list
    )
    assert view.mentioned_repositories.inaccessible == []
    assert view.mentioned_repositories.on_it_message(primary) == (
        f"I'm on it in {primary}, together with {other}!"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'make,module,provider,primary,other', RESOLVERS, ids=RESOLVER_IDS
)
async def test_event_naming_no_other_repository_starts_as_before(
    make, module, provider, primary, other
):
    view = make('@openhands please fix this')
    view.description = 'The build is broken.'

    request, create_handler = await _start(view, module, _handler({}))

    assert request.selected_repository == primary
    assert request.additional_repositories is None
    create_handler.assert_not_awaited()
    assert view.mentioned_repositories.on_it_message(primary) == "I'm on it!"
    assert view.mentioned_repositories.notes() == ''


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'make,module,provider,primary,other', RESOLVERS, ids=RESOLVER_IDS
)
async def test_inaccessible_comment_mention_is_reported_and_start_continues(
    make, module, provider, primary, other
):
    view = make(
        f'@openhands use {_mention(other)}, src/components and '
        'https://example.com/acme/secret'
    )

    request, _ = await _start(
        view, module, _handler({other.lower(): _repo(other, provider)})
    )

    assert [r.repository for r in request.additional_repositories] == [other]
    assert view.mentioned_repositories.inaccessible == ['acme/secret']
    assert 'Could not access: acme/secret.' in view.mentioned_repositories.notes()


@pytest.mark.asyncio
async def test_verification_failure_starts_with_primary_only():
    view = _github('@openhands see acme/api')
    handler = MagicMock()
    handler.verify_repo_provider = AsyncMock(side_effect=RuntimeError('boom'))
    with patch(
        'integrations.mentioned_repositories.verify_mentioned_repos',
        AsyncMock(side_effect=RuntimeError('boom')),
    ):
        request, _ = await _start(view, 'integrations.github.github_view', handler)
    assert request.selected_repository == 'acme/web'
    assert request.additional_repositories is None


@pytest.mark.asyncio
async def test_github_labeled_issue_ignores_issue_body_mentions():
    """A labeled issue has no comment, so it starts with the primary only."""
    view = GithubIssue(
        **{**COMMON, 'installation_id': 1}, full_repo_name='acme/web', uuid=None
    )
    view.description = 'Touches acme/api and acme/gone.'
    handler = _handler({'acme/api': _repo('acme/api', ProviderType.GITHUB)})

    request, create_handler = await _start(
        view, 'integrations.github.github_view', handler
    )

    assert request.additional_repositories is None
    create_handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_azure_work_item_comment_with_azure_url_mention():
    view = AzureDevOpsWorkItemComment(
        **COMMON,
        full_repo_name='org/proj/web',
        project_name='proj',
        project_id='p1',
        repository_id='r1',
        comment_body=(
            '<div>@openhands also change '
            '<a href="https://dev.azure.com/org/proj/_git/api">api</a></div>'
        ),
    )
    handler = _handler(
        {'org/proj/api': _repo('org/proj/api', ProviderType.AZURE_DEVOPS)}
    )

    request, _ = await _start(
        view, 'integrations.azure_devops.azure_devops_view', handler
    )

    assert request.selected_repository == 'org/proj/web'
    assert [r.repository for r in request.additional_repositories] == ['org/proj/api']


MANAGERS = [
    ('integrations.github.github_manager', 'GithubManager'),
    ('integrations.gitlab.gitlab_manager', 'GitlabManager'),
    ('integrations.bitbucket.bitbucket_manager', 'BitbucketManager'),
    ('integrations.bitbucket_data_center.bitbucket_dc_manager', 'BitbucketDCManager'),
    ('integrations.azure_devops.azure_devops_manager', 'AzureDevOpsManager'),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'resolver,manager_path',
    list(zip(RESOLVERS, MANAGERS, strict=False)),
    ids=RESOLVER_IDS,
)
async def test_acknowledgement_names_the_repositories(resolver, manager_path):
    make, _, provider, primary, other = resolver
    module_name, class_name = manager_path
    module = __import__(module_name, fromlist=[class_name])
    token_manager = MagicMock()
    token_manager.get_idp_token_from_idp_user_id = AsyncMock(return_value='token')
    token_manager.get_idp_token_for_user = AsyncMock(return_value='token')
    data_collector = MagicMock()
    data_collector.save_data = AsyncMock()
    # GithubManager builds a GitHub App client from its credentials.
    with (
        patch.object(module, 'Auth', MagicMock(), create=True),
        patch.object(module, 'GithubIntegration', MagicMock(), create=True),
    ):
        manager = getattr(module, class_name)(token_manager, data_collector)

    view = make(f'@openhands use {other} and acme/secret')
    view.initialize_new_conversation = AsyncMock(return_value=uuid4())

    async def _create(*args):
        view.mentioned_repositories = MentionedRepositories(
            additional=[_repo(other, provider)], inaccessible=['acme/secret']
        )

    view.create_new_conversation = _create
    with (
        patch(f'{module_name}.get_saas_user_auth', AsyncMock()),
        patch.object(manager, 'send_message', new=AsyncMock()) as send,
    ):
        await manager.start_job(view)

    message = send.await_args.args[0]
    assert message.startswith(f"I'm on it in {primary}, together with {other}! ")
    assert 'Could not access: acme/secret.' in message
