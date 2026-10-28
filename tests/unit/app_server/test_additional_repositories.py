"""Tests for starting a conversation with additional repositories."""

from contextlib import asynccontextmanager
from types import MethodType
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openhands.agent_server.models import ConversationInfo
from openhands.app_server.app_conversation.additional_repositories import (
    AdditionalRepository,
    AdditionalRepositoryRequest,
    AdditionalRepositoryStatus,
    agent_working_dir,
    assign_repository_folders,
    loads_repository_hooks,
    merge_repository_skills,
    repositories_context,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
    AppConversationStartRequest,
    AppConversationStartTask,
    ConversationPullRequest,
)
from openhands.app_server.app_conversation.app_conversation_service_base import (
    AppConversationServiceBase,
)
from openhands.app_server.app_conversation.live_status_app_conversation_service import (
    LiveStatusAppConversationService,
    azure_devops_context,
)
from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    SQLAppConversationInfoService,
)
from openhands.app_server.event_callback.webhook_router import on_conversation_update
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.mcp import mcp_router
from openhands.app_server.sandbox.sandbox_models import SandboxRecord
from openhands.app_server.user.specifiy_user_context import SpecifyUserContext
from openhands.sdk.conversation import ConversationExecutionStatus
from openhands.sdk.skills import KeywordTrigger, PathTrigger, Skill
from server.services.org_conversation_service import conversation_pr_keys

ADO = ProviderType.AZURE_DEVOPS
GITHUB = ProviderType.GITHUB


def _extra(repository: str, provider=ADO, branch=None) -> AdditionalRepositoryRequest:
    return AdditionalRepositoryRequest(
        repository=repository, branch=branch, git_provider=provider
    )


def _repo(
    repository: str,
    folder: str,
    status=AdditionalRepositoryStatus.CLONED,
    provider=ADO,
) -> AdditionalRepository:
    return AdditionalRepository(
        repository=repository,
        git_provider=provider,
        folder=folder,
        path=f'/workspace/project/{folder}',
        status=status,
        error=None if status == AdditionalRepositoryStatus.CLONED else 'no access',
    )


# --- request validation ------------------------------------------------------


def _start_request(**kwargs) -> AppConversationStartRequest:
    return AppConversationStartRequest(
        selected_repository='org1/proj/main',
        git_provider=ADO,
        **kwargs,
    )


def test_start_request_accepts_additional_repositories():
    request = _start_request(
        additional_repositories=[
            {'repository': 'org2/proj/lib', 'git_provider': 'azure_devops'},
            {'repository': 'owner/tool', 'branch': 'dev', 'git_provider': 'github'},
        ]
    )

    assert [r.repository for r in request.additional_repositories] == [
        'org2/proj/lib',
        'owner/tool',
    ]


@pytest.mark.parametrize(
    'additional',
    [
        # duplicates the main repository, ignoring case
        [{'repository': 'ORG1/proj/main', 'git_provider': 'azure_devops'}],
        # listed twice
        [
            {'repository': 'org2/p/lib', 'git_provider': 'azure_devops'},
            {'repository': 'org2/p/lib', 'git_provider': 'azure_devops'},
        ],
        # more than nine
        [
            {'repository': f'org2/p/r{i}', 'git_provider': 'azure_devops'}
            for i in range(10)
        ],
        [{'repository': 'no-owner', 'git_provider': 'github'}],
        [{'repository': 'owner/..', 'git_provider': 'github'}],
        [{'repository': 'owner/repo;rm -rf', 'git_provider': 'github'}],
        [
            {
                'repository': 'owner/repo',
                'branch': 'bad..branch',
                'git_provider': 'github',
            }
        ],
        [{'repository': 'owner/repo'}],
    ],
)
def test_start_request_rejects_invalid_additional_repositories(additional):
    with pytest.raises(ValidationError):
        _start_request(additional_repositories=additional)


def test_additional_repositories_without_a_primary_are_accepted():
    request = AppConversationStartRequest(
        additional_repositories=[
            {'repository': 'a/b', 'git_provider': 'github'},
            {'repository': 'c/b', 'git_provider': 'github'},
        ]
    )

    assert request.selected_repository is None
    assert assign_repository_folders(None, request.additional_repositories) == [
        'b',
        'c-b',
    ]


def test_same_name_on_another_provider_is_not_a_duplicate():
    request = AppConversationStartRequest(
        selected_repository='owner/repo',
        git_provider=GITHUB,
        additional_repositories=[
            {'repository': 'owner/repo', 'git_provider': 'gitlab'}
        ],
    )
    assert len(request.additional_repositories) == 1


# --- folders -----------------------------------------------------------------


def test_same_named_repositories_get_distinct_folders():
    folders = assign_repository_folders(
        'org1/proj/app',
        [
            _extra('org2/proj/app'),
            _extra('org1/other/app'),
            _extra('org2/proj/lib'),
            _extra('org2/proj/App'),
        ],
    )

    assert folders == ['org2-proj-app', 'org1-other-app', 'lib', 'org2-proj-App-2']
    assert len({f.lower() for f in folders} | {'app'}) == 5


def test_folder_names_are_shell_safe():
    folders = assign_repository_folders(
        'owner/main', [_extra('org/proj/My Repo (old)')]
    )
    assert folders == ['My-Repo-old']


# --- clone -------------------------------------------------------------------


class _Result:
    def __init__(self, exit_code=0, stdout='', stderr=''):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def _clone_service(urls: dict[str, str | None], bearer: str | None = None):
    user_context = MagicMock()
    user_context.get_user_info = AsyncMock(return_value=Mock(git_full_clone=False))

    async def authenticated_url(repository, specified_provider=None):
        url = urls[repository]
        if url == 'raise':
            raise RuntimeError('repository not found for token')
        return url

    user_context.get_authenticated_git_url = AsyncMock(side_effect=authenticated_url)
    user_context.get_latest_token = AsyncMock(return_value=bearer)
    service = MagicMock()
    service.user_context = user_context
    for name in (
        'clone_additional_repositories',
        '_clone_additional_repository',
        '_clone_repository',
        '_get_azure_devops_bearer_token_for_git',
    ):
        setattr(
            service,
            name,
            MethodType(getattr(AppConversationServiceBase, name), service),
        )
    return service


def _workspace(failing_urls: set[str]):
    workspace = MagicMock()
    workspace.working_dir = '/workspace/project'

    async def execute_command(command, cwd=None, timeout=None):
        if ' clone ' in command and any(url in command for url in failing_urls):
            return _Result(128, stderr='fatal: https://user:secret@host denied')
        if command.startswith('test -d '):
            return _Result(1)
        return _Result()

    workspace.execute_command = AsyncMock(side_effect=execute_command)
    return workspace


@pytest.mark.asyncio
async def test_failed_additional_repository_is_reported_and_does_not_stop_others():
    service = _clone_service(
        {
            'org2/proj/app': 'https://dev.azure.com/org2/proj/_git/app',
            'org3/proj/secret': 'https://dev.azure.com/org3/proj/_git/secret',
            'owner/gone': 'raise',
            'owner/tool': 'https://x:token@github.com/owner/tool.git',
        },
        bearer='eyJ.entra.jwt',
    )
    workspace = _workspace({'https://dev.azure.com/org3/proj/_git/secret'})
    task = AppConversationStartTask(
        created_by_user_id='u',
        request=AppConversationStartRequest(
            selected_repository='org1/proj/app',
            git_provider=ADO,
            additional_repositories=[
                _extra('org2/proj/app'),
                _extra('org3/proj/secret'),
                _extra('owner/gone', GITHUB),
                _extra('owner/tool', GITHUB, branch='dev'),
            ],
        ),
    )

    results = await service.clone_additional_repositories(task, workspace)

    assert [(r.folder, r.status) for r in results] == [
        ('org2-proj-app', AdditionalRepositoryStatus.CLONED),
        ('secret', AdditionalRepositoryStatus.FAILED),
        ('gone', AdditionalRepositoryStatus.FAILED),
        ('tool', AdditionalRepositoryStatus.CLONED),
    ]
    assert results[0].path == '/workspace/project/org2-proj-app'
    for failed in results[1:3]:
        assert 'Could not clone' in failed.error
        assert 'secret@' not in failed.error and 'token' not in failed.error
    commands = [c.args[0] for c in workspace.execute_command.call_args_list]
    ado_clone = next(c for c in commands if 'org2/proj/_git/app' in c)
    # Entra tokens ride a header, as for the main repository.
    assert 'http.extraheader=' in ado_clone and ado_clone.endswith(' org2-proj-app')
    assert any(
        c.startswith('git clone --depth 1 --branch dev ') and c.endswith(' tool')
        for c in commands
    )


@pytest.mark.asyncio
async def test_no_additional_repositories_clones_nothing():
    service = _clone_service({})
    workspace = _workspace(set())
    task = AppConversationStartTask(
        created_by_user_id='u',
        request=AppConversationStartRequest(selected_repository='a/b'),
    )

    assert await service.clone_additional_repositories(task, workspace) == []
    workspace.execute_command.assert_not_called()


@pytest.mark.asyncio
async def test_setup_records_failures_in_the_start_task_detail():
    service = MagicMock()
    service.run_setup_scripts = MethodType(
        AppConversationServiceBase.run_setup_scripts, service
    )
    for name in (
        'clone_or_init_git_repo',
        '_configure_azure_devops_git_credentials',
        '_configure_git_credential_helpers',
        'maybe_run_setup_script',
        'maybe_setup_git_hooks',
        'maybe_inject_memory_context',
        'load_and_merge_all_skills',
    ):
        setattr(service, name, AsyncMock())
    service.clone_additional_repositories = AsyncMock(
        return_value=[
            _repo('org2/p/lib', 'lib'),
            _repo('org3/p/x', 'x', AdditionalRepositoryStatus.FAILED),
        ]
    )
    task = AppConversationStartTask(
        created_by_user_id='u',
        request=_start_request(additional_repositories=[_extra('org2/p/lib')]),
    )
    workspace = MagicMock(working_dir='/workspace/project')

    async for _ in service.run_setup_scripts(task, Mock(), workspace, 'url', uuid4()):
        pass

    assert task.detail == 'Could not clone additional repositories: org3/p/x'
    assert [r.folder for r in task._additional_repositories] == ['lib', 'x']
    # Every Azure DevOps repository's organization gets the credential helper.
    assert service._configure_azure_devops_git_credentials.call_args.args[3] == [
        'org2/p/lib'
    ]
    # setup.sh still runs from the main repository only.
    service.maybe_run_setup_script.assert_awaited_once_with(
        workspace, '/workspace/project/main'
    )


# --- skills ------------------------------------------------------------------


def _skill(name, source, trigger=None, content='x'):
    return Skill(name=name, content=content, source=source, trigger=trigger)


def test_every_repository_agents_md_survives_and_main_wins_conflicts():
    main_dir = '/workspace/project/app'
    general = [
        _skill('github', '/public/github.md', KeywordTrigger(keywords=['gh'])),
        _skill('agents', f'{main_dir}/AGENTS.md', content='main rules'),
        _skill('deploy', f'{main_dir}/.agents/skills/deploy.md'),
        _skill(
            'agents:server',
            f'{main_dir}/server/AGENTS.md',
            PathTrigger(paths=['server/**']),
        ),
    ]
    lib = _repo('org2/p/lib', 'lib')
    tool = _repo('org3/p/tool', 'tool')
    lib_skills = [
        _skill('agents', '/workspace/project/lib/AGENTS.md', content='lib rules'),
        _skill('deploy', '/workspace/project/lib/.agents/skills/deploy.md'),
        _skill('github', '/workspace/project/lib/.agents/skills/github.md'),
        _skill(
            'rule',
            '/workspace/project/lib/.agents/skills/rule.md',
            PathTrigger(paths=['./src/*.py', '.github/**']),
        ),
    ]
    tool_skills = [
        _skill('claude', '/workspace/project/tool/CLAUDE.md', content='tool rules'),
        _skill('github', '/workspace/project/tool/.agents/skills/github.md'),
    ]

    merged = {
        s.name: s
        for s in merge_repository_skills(
            general, main_dir, 'app', [(lib, lib_skills), (tool, tool_skills)]
        )
    }

    assert merged['agents'].content == 'main rules'
    assert merged['agents:lib'].content == 'lib rules'
    assert merged['claude:tool'].content == 'tool rules'
    # Main project skill wins; a repository's project skill beats a public one
    # and the earlier repository wins between projects.
    assert merged['deploy'].source.startswith(main_dir)
    assert merged['github'].source == '/workspace/project/lib/.agents/skills/github.md'
    # Path rules match relative to the parent working folder.
    assert merged['agents:server'].trigger.paths == ['app/server/**']
    assert merged['rule'].trigger.paths == ['lib/src/*.py', 'lib/.github/**']


@pytest.mark.asyncio
async def test_project_skills_load_once_per_cloned_repository():
    service = MagicMock()
    service.user_context = MagicMock()
    service._configure_marketplace_git_credentials = AsyncMock()
    service.load_and_merge_all_skills = MethodType(
        AppConversationServiceBase.load_and_merge_all_skills, service
    )
    calls = []

    async def fake_load(**kwargs):
        calls.append(kwargs)
        name = kwargs['project_dir'].rsplit('/', 1)[-1]
        return [_skill('agents', f'{kwargs["project_dir"]}/AGENTS.md', content=name)]

    sandbox = Mock(session_api_key='k', exposed_urls=[])
    base = 'openhands.app_server.app_conversation.app_conversation_service_base'
    with (
        patch(f'{base}.load_skills_from_agent_server', side_effect=fake_load),
        patch(f'{base}.build_org_configs', AsyncMock(return_value=[])) as org_configs,
        patch(f'{base}.build_sandbox_config', return_value=None),
        patch(f'{base}.authenticate_marketplace_sources', AsyncMock(return_value=None)),
    ):
        skills = await service.load_and_merge_all_skills(
            sandbox,
            'org1/p/app',
            '/workspace/project/app',
            'http://agent',
            additional_repositories=[
                _repo('org2/p/lib', 'lib'),
                _repo('org3/p/x', 'x', AdditionalRepositoryStatus.FAILED),
            ],
        )

    assert {s.name: s.content for s in skills} == {'agents': 'app', 'agents:lib': 'lib'}
    assert [c['project_dir'] for c in calls] == [
        '/workspace/project/app',
        '/workspace/project/lib',
    ]
    assert (
        calls[1]['load_public']
        is calls[1]['load_user']
        is calls[1]['load_org']
        is False
    )
    # Org skill candidates include every repository's owner.
    assert org_configs.call_args.args[2] == ['org2/p/lib', 'org3/p/x']


# --- agent context ---------------------------------------------------------


def test_repositories_block_lists_paths_branches_and_status():
    block = repositories_context(
        'org1/p/app',
        'main',
        ADO,
        '/workspace/project/app',
        [
            _repo('org2/p/lib', 'lib'),
            _repo('org3/p/x', 'x', AdditionalRepositoryStatus.FAILED),
        ],
    )

    assert block.startswith('<REPOSITORIES>\n') and block.endswith('</REPOSITORIES>')
    assert (
        '- `org1/p/app` at `/workspace/project/app`, branch `main`, '
        'provider azure_devops, main repository'
    ) in block
    assert (
        '- `org2/p/lib` at `/workspace/project/lib`, provider azure_devops, cloned'
        in block
    )
    assert (
        '`org3/p/x` at `/workspace/project/x`, provider azure_devops, not cloned: no access'
        in block
    )


def test_azure_devops_context_covers_additional_repositories():
    context = azure_devops_context(
        None, ['org2'], True, additional_repositories=['org2/p/lib']
    )

    assert 'Azure DevOps repositories in this conversation are hosted at' in context
    assert (
        'Additional repository: organization `org2`, project `p`, repository `lib` '
        '(https://dev.azure.com/org2/p/_git/lib).'
    ) in context


@pytest.mark.asyncio
async def test_git_context_adds_repositories_and_azure_devops_blocks():
    service = MagicMock()
    service.user_context = MagicMock()
    for name in (
        '_append_git_context',
        '_maybe_append_azure_devops_context',
        '_maybe_append_shallow_clone_context',
    ):
        setattr(
            service,
            name,
            MethodType(getattr(LiveStatusAppConversationService, name), service),
        )
    with patch(
        'openhands.app_server.app_conversation.live_status_app_conversation_service.azure_devops_user_orgs',
        AsyncMock(return_value=['org2']),
    ) as orgs:
        suffix = await service._append_git_context(
            Mock(git_full_clone=True),
            GITHUB,
            'owner/app',
            None,
            'jwt',
            None,
            '/workspace/project',
            [_repo('org2/p/lib', 'lib')],
        )

    assert '<AZURE_DEVOPS_CONTEXT>' in suffix and '<REPOSITORIES>' in suffix
    assert '`owner/app` at `/workspace/project/app`' in suffix
    orgs.assert_awaited_once_with(service.user_context, None, ['org2/p/lib'])


@pytest.mark.asyncio
async def test_git_context_is_unchanged_without_additional_repositories():
    service = MagicMock()
    service._append_git_context = MethodType(
        LiveStatusAppConversationService._append_git_context, service
    )
    service._maybe_append_shallow_clone_context = MethodType(
        LiveStatusAppConversationService._maybe_append_shallow_clone_context, service
    )
    service._maybe_append_azure_devops_context = MethodType(
        LiveStatusAppConversationService._maybe_append_azure_devops_context, service
    )

    suffix = await service._append_git_context(
        Mock(git_full_clone=True), GITHUB, 'owner/app', None, None, 'x', '/w', []
    )

    assert suffix == 'x'


# --- persistence -------------------------------------------------------------


@pytest.fixture
async def info_service(async_engine):
    maker = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with maker() as session:
        yield SQLAppConversationInfoService(
            db_session=session, user_context=SpecifyUserContext(user_id='user_123')
        )


@asynccontextmanager
async def _event_callback_service(state, request=None):
    yield AsyncMock()


@pytest.mark.asyncio
async def test_repositories_and_pull_requests_survive_the_lifecycle_webhook(
    info_service,
):
    conversation_id = uuid4()
    existing = AppConversationInfo(
        id=conversation_id,
        title='multi',
        sandbox_id='sandbox_123',
        created_by_user_id='user_123',
        selected_repository='org1/p/app',
        git_provider=ADO,
        pr_number=[7],
        pull_requests=[
            ConversationPullRequest(number=7, repository='org2/p/lib', git_provider=ADO)
        ],
        additional_repositories=[
            _repo('org2/p/lib', 'lib'),
            _repo('org3/p/x', 'x', AdditionalRepositoryStatus.FAILED),
        ],
    )
    await info_service.save_app_conversation_info(existing)
    conversation_info = MagicMock(spec=ConversationInfo)
    conversation_info.id = conversation_id
    conversation_info.execution_status = ConversationExecutionStatus.RUNNING
    conversation_info.agent = MagicMock()
    conversation_info.agent.llm.model = 'gpt-4'
    conversation_info.stats = MagicMock()
    conversation_info.stats.get_combined_metrics.return_value = None
    conversation_info.tags = {}

    with (
        patch(
            'openhands.app_server.event_callback.webhook_router.valid_conversation',
            return_value=await info_service.get_app_conversation_info(conversation_id),
        ),
        patch(
            'openhands.app_server.event_callback.webhook_router.get_event_callback_service',
            _event_callback_service,
        ),
    ):
        await on_conversation_update(
            conversation_info=conversation_info,
            sandbox_record=SandboxRecord(
                id='sandbox_123', created_by_user_id='user_123'
            ),
            app_conversation_info_service=info_service,
        )

    saved = await info_service.get_app_conversation_info(conversation_id)
    assert saved.additional_repositories == existing.additional_repositories
    assert saved.pull_requests == existing.pull_requests


# --- parent inheritance -----------------------------------------------------


def test_sub_conversation_inherits_additional_repositories():
    service = MagicMock()
    parent = AppConversationInfo(
        sandbox_id='s',
        created_by_user_id='u',
        selected_repository='org1/p/app',
        git_provider=ADO,
        additional_repositories=[_repo('org2/p/lib', 'lib')],
    )
    request = AppConversationStartRequest()

    LiveStatusAppConversationService._inherit_configuration_from_parent(
        service, request, parent
    )

    assert request.selected_repository == 'org1/p/app'
    assert request.additional_repositories == [_extra('org2/p/lib')]


# --- PR attribution ---------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_tool_records_the_repository_of_the_pull_request():
    info = AppConversationInfo(
        sandbox_id='s',
        created_by_user_id='u',
        selected_repository='org1/p/app',
        git_provider=ADO,
    )
    service = AsyncMock()
    service.get_app_conversation_info = AsyncMock(return_value=info)

    @asynccontextmanager
    async def get_service(state):
        yield service

    with patch.object(mcp_router, 'get_app_conversation_info_service', get_service):
        await mcp_router.save_pr_metadata(
            None,
            str(info.id),
            'https://dev.azure.com/org2/p/_git/lib/pullrequest/12',
            'org2/p/lib',
            ADO,
        )

    saved = service.save_app_conversation_info.call_args.args[0]
    assert saved.pr_number == [12]
    assert saved.pull_requests == [
        ConversationPullRequest(number=12, repository='org2/p/lib', git_provider=ADO)
    ]


def test_gitlab_project_reference_resolves_to_its_path():
    assert mcp_router._gitlab_repository('group%2Fsub%2Frepo') == 'group/sub/repo'
    assert mcp_router._gitlab_repository(42) is None


def test_pr_merge_keys_use_each_pull_requests_repository():
    keys = conversation_pr_keys(
        'org1/p/app',
        'azure_devops',
        [3, 12],
        [{'number': 12, 'repository': 'org2/p/lib', 'git_provider': 'azure_devops'}],
    )

    assert keys == [
        ('azure_devops', 'org2/p/lib', 12),
        ('azure_devops', 'org1/p/app', 3),
    ]
    # Rows recorded before pull_requests existed keep the main repository.
    assert conversation_pr_keys('o/r', 'github', [5], None) == [('github', 'o/r', 5)]


# --- primary-only behaviour and the single-repository invariants -------------


def _setup_service(clone_results):
    service = MagicMock()
    service.run_setup_scripts = MethodType(
        AppConversationServiceBase.run_setup_scripts, service
    )
    for name in (
        'clone_or_init_git_repo',
        '_configure_azure_devops_git_credentials',
        '_configure_git_credential_helpers',
        'maybe_run_setup_script',
        'maybe_setup_git_hooks',
        'maybe_inject_memory_context',
        'load_and_merge_all_skills',
    ):
        setattr(service, name, AsyncMock())
    service.clone_additional_repositories = AsyncMock(return_value=clone_results)
    return service


async def _run_setup(service, request):
    task = AppConversationStartTask(created_by_user_id='u', request=request)
    workspace = MagicMock(working_dir='/workspace/project')
    async for _ in service.run_setup_scripts(task, Mock(), workspace, 'url', uuid4()):
        pass
    return task, workspace


@pytest.mark.asyncio
async def test_require_all_fails_the_start_when_a_repository_does_not_clone():
    service = _setup_service(
        [
            _repo('org2/p/lib', 'lib'),
            _repo('org3/p/x', 'x', AdditionalRepositoryStatus.FAILED),
        ]
    )
    request = _start_request(
        additional_repositories=[_extra('org2/p/lib'), _extra('org3/p/x')],
        require_all=True,
    )

    with pytest.raises(ValueError, match='org3/p/x'):
        await _run_setup(service, request)
    service.maybe_run_setup_script.assert_not_awaited()


@pytest.mark.asyncio
async def test_pre_commit_is_installed_in_every_cloned_repository():
    service = _setup_service(
        [
            _repo('org2/p/lib', 'lib'),
            _repo('org3/p/x', 'x', AdditionalRepositoryStatus.FAILED),
        ]
    )

    _, workspace = await _run_setup(
        service,
        _start_request(
            additional_repositories=[_extra('org2/p/lib'), _extra('org3/p/x')]
        ),
    )

    assert [c.args[1] for c in service.maybe_setup_git_hooks.await_args_list] == [
        '/workspace/project/main',
        '/workspace/project/lib',
    ]


@pytest.mark.asyncio
async def test_parent_folder_is_not_git_initialised_without_a_primary():
    service, user_context = MagicMock(), MagicMock()
    user_context.get_user_info = AsyncMock(
        return_value=Mock(git_user_name=None, git_user_email=None)
    )
    user_context.get_secrets = AsyncMock(return_value={})
    service.user_context = user_context
    service.init_git_in_empty_workspace = True
    for name in (
        'clone_or_init_git_repo',
        '_configure_git_user_settings',
        '_configure_gpg_signing',
    ):
        setattr(
            service,
            name,
            MethodType(getattr(AppConversationServiceBase, name), service),
        )
    workspace = _workspace(set())
    task = AppConversationStartTask(
        created_by_user_id='u',
        request=AppConversationStartRequest(
            additional_repositories=[_extra('org2/p/lib')]
        ),
    )

    await service.clone_or_init_git_repo(task, workspace)

    assert not any(
        'git init' in c.args[0] for c in workspace.execute_command.call_args_list
    )


@pytest.mark.asyncio
async def test_repositories_clone_without_a_primary():
    service = _clone_service({'org2/p/app': 'https://dev.azure.com/org2/p/_git/app'})
    task = AppConversationStartTask(
        created_by_user_id='u',
        request=AppConversationStartRequest(
            additional_repositories=[_extra('org2/p/app')]
        ),
    )

    [repo] = await service.clone_additional_repositories(task, _workspace(set()))

    assert (repo.folder, repo.status) == ('app', AdditionalRepositoryStatus.CLONED)


def test_repositories_read_field_lists_the_primary_first():
    info = AppConversationInfo(
        sandbox_id='s',
        created_by_user_id='u',
        selected_repository='org1/p/app',
        selected_branch='main',
        git_provider=ADO,
        additional_repositories=[
            _repo('org3/p/x', 'x', AdditionalRepositoryStatus.FAILED)
        ],
        tags={'archiveworkspacepath': '/workspace/project'},
    )

    listed = info.model_dump(mode='json')['repositories']

    assert [
        (r['repository'], r['primary'], r['status'], r['path']) for r in listed
    ] == [
        ('org1/p/app', True, 'cloned', '/workspace/project/app'),
        ('org3/p/x', False, 'failed', '/workspace/project/x'),
    ]
    no_primary = AppConversationInfo(
        sandbox_id='s',
        created_by_user_id='u',
        additional_repositories=[_repo('a/b', 'b')],
    )
    assert [r.primary for r in no_primary.repositories] == [False]


def test_repositories_block_without_a_primary():
    block = repositories_context(
        None, None, None, '/workspace/project', [_repo('a/b', 'b')]
    )

    assert ', main repository' not in block
    assert "No repository's setup.sh has been run." in block
    assert '`a/b` at `/workspace/project/b`' in block


def test_hooks_are_skipped_only_with_additional_repositories():
    assert loads_repository_hooks([]) is True
    assert loads_repository_hooks([_repo('a/b', 'b')]) is False


# Single-repository invariants: without additional repositories the workspace
# folder, skill loading, hooks and PR metadata are what they were before.


def test_single_repository_invariant_workspace_folder():
    assert agent_working_dir('/workspace/project', '/workspace/project/app', []) == (
        '/workspace/project/app'
    )
    assert agent_working_dir('/workspace/project', '/workspace/project', []) == (
        '/workspace/project'
    )


@pytest.mark.asyncio
async def test_single_repository_invariant_skill_set():
    service = MagicMock()
    service.user_context = MagicMock()
    service._configure_marketplace_git_credentials = AsyncMock()
    service.load_and_merge_all_skills = MethodType(
        AppConversationServiceBase.load_and_merge_all_skills, service
    )
    loaded = [
        _skill(
            'agents', '/workspace/project/app/AGENTS.md', PathTrigger(paths=['x/**'])
        )
    ]
    base = 'openhands.app_server.app_conversation.app_conversation_service_base'
    with (
        patch(
            f'{base}.load_skills_from_agent_server', AsyncMock(return_value=loaded)
        ) as load,
        patch(f'{base}.build_org_configs', AsyncMock(return_value=[])),
        patch(f'{base}.build_sandbox_config', return_value=None),
        patch(f'{base}.authenticate_marketplace_sources', AsyncMock(return_value=None)),
    ):
        skills = await service.load_and_merge_all_skills(
            Mock(session_api_key='k', exposed_urls=[]),
            'org1/p/app',
            '/workspace/project/app',
            'http://agent',
        )

    assert skills == loaded
    load.assert_awaited_once()
    assert load.await_args.kwargs['load_project'] is True
    assert load.await_args.kwargs['project_dir'] == '/workspace/project/app'


def test_single_repository_invariant_hooks():
    assert loads_repository_hooks([]) is True


@pytest.mark.asyncio
async def test_single_repository_invariant_setup():
    service = _setup_service([])

    task, workspace = await _run_setup(service, _start_request())

    assert task.detail is None
    service.maybe_run_setup_script.assert_awaited_once_with(
        workspace, '/workspace/project/main'
    )
    assert [c.args[1] for c in service.maybe_setup_git_hooks.await_args_list] == [
        '/workspace/project/main'
    ]


def test_single_repository_invariant_pr_metadata():
    assert conversation_pr_keys(
        'OpenHands/app',
        'github',
        [5],
        [{'number': 5, 'repository': 'OpenHands/app', 'git_provider': 'github'}],
    ) == conversation_pr_keys('OpenHands/app', 'github', [5], None)


def test_pr_in_the_main_repository_keeps_its_stored_casing():
    # GitHub accepts any casing in the PR tool; openhands_prs stores the canonical name.
    keys = conversation_pr_keys(
        'OpenHands/enterprise',
        'github',
        [5],
        [{'number': 5, 'repository': 'openhands/enterprise', 'git_provider': 'github'}],
    )

    assert keys == [('github', 'OpenHands/enterprise', 5)]


@pytest.mark.asyncio
async def test_main_path_rules_are_rescoped_when_no_additional_repository_cloned():
    service = MagicMock()
    service.user_context = MagicMock()
    service._configure_marketplace_git_credentials = AsyncMock()
    service.load_and_merge_all_skills = MethodType(
        AppConversationServiceBase.load_and_merge_all_skills, service
    )
    main_rule = _skill(
        'agents:server',
        '/workspace/project/app/server/AGENTS.md',
        PathTrigger(paths=['server/**']),
    )
    base = 'openhands.app_server.app_conversation.app_conversation_service_base'
    with (
        patch(
            f'{base}.load_skills_from_agent_server',
            AsyncMock(return_value=[main_rule]),
        ) as load,
        patch(f'{base}.build_org_configs', AsyncMock(return_value=[])),
        patch(f'{base}.build_sandbox_config', return_value=None),
        patch(f'{base}.authenticate_marketplace_sources', AsyncMock(return_value=None)),
    ):
        skills = await service.load_and_merge_all_skills(
            Mock(session_api_key='k', exposed_urls=[]),
            'org1/p/app',
            '/workspace/project/app',
            'http://agent',
            additional_repositories=[
                _repo('org3/p/x', 'x', AdditionalRepositoryStatus.FAILED)
            ],
        )

    # The agent works from the parent folder, so the main rule must name its folder.
    assert skills[0].trigger.paths == ['app/server/**']
    assert load.await_count == 1
