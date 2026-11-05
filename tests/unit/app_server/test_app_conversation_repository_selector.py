"""The read endpoints of a multi-repository conversation.

``/files``, ``/file``, ``/git/changes``, ``/git/diff``, ``/skills`` and
``/hooks`` resolve paths against the conversation's roots: the primary
repository's folder by default, any other cloned repository through the
``repository`` selector (its folder name), and the working folder when there is
no primary. Single-repository conversations keep their previous behaviour.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, status

from openhands.app_server.app_conversation.additional_repositories import (
    AdditionalRepository,
    AdditionalRepositoryStatus,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversation,
    AppConversationInfo,
)
from openhands.app_server.app_conversation.app_conversation_router import (
    AgentServerContext,
    _resolve_file_path,
    _resolve_workspace_dir,
    get_conversation_git_changes,
    get_conversation_git_diff,
    get_conversation_hooks,
    get_conversation_skills,
    list_conversation_files,
    read_conversation_file,
)
from openhands.app_server.app_conversation.app_conversation_service_base import (
    AppConversationServiceBase,
    get_project_dir,
)
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    ExposedUrl,
    SandboxInfo,
    SandboxStatus,
)
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.user.user_context import UserContext
from openhands.sdk.skills import Skill

ADO = ProviderType.AZURE_DEVOPS
ROUTER = 'openhands.app_server.app_conversation.app_conversation_router.'
WORKING_DIR = '/workspace/project'


def _repo(
    repository: str,
    folder: str,
    status=AdditionalRepositoryStatus.CLONED,
    working_dir: str = WORKING_DIR,
) -> AdditionalRepository:
    return AdditionalRepository(
        repository=repository,
        git_provider=ADO,
        folder=folder,
        path=f'{working_dir}/{folder}',
        status=status,
        error=None if status == AdditionalRepositoryStatus.CLONED else 'no access',
    )


def _info(
    selected_repository: str | None = 'org1/p/app',
    additional=(),
    conversation_id=None,
    sandbox_id=None,
    **kwargs,
) -> AppConversationInfo:
    return AppConversationInfo(
        id=conversation_id or uuid4(),
        created_by_user_id='u',
        sandbox_id=sandbox_id or str(uuid4()),
        selected_repository=selected_repository,
        git_provider=ADO if selected_repository else None,
        additional_repositories=list(additional),
        **kwargs,
    )


def _ctx(
    selected_repository: str | None = 'org1/p/app',
    additional=(),
    working_dir: str = WORKING_DIR,
) -> AgentServerContext:
    spec = MagicMock()
    spec.working_dir = working_dir
    return AgentServerContext(
        conversation=_info(selected_repository, additional),
        sandbox=MagicMock(status=SandboxStatus.RUNNING),
        sandbox_spec=spec,
        agent_server_url='http://agent.test',
        session_api_key='sess-key',
    )


def _bash_client(stdout: str = '') -> AsyncMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(
        return_value={'exit_code': 0, 'stdout': stdout, 'stderr': ''}
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=response)
    return client


def _get_client(payload) -> AsyncMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=response)
    return client


# --- roots -------------------------------------------------------------------


def test_roots_list_the_primary_then_each_cloned_repository():
    from openhands.app_server.app_conversation.additional_repositories import (
        conversation_roots,
        select_root,
    )

    roots = conversation_roots(
        WORKING_DIR,
        'org1/p/app',
        [_repo('org2/p/lib', 'lib'), _repo('org3/p/x', 'x', 'failed')],
    )

    assert [(r.path, r.folder, r.primary) for r in roots] == [
        (f'{WORKING_DIR}/app', 'app', True),
        (f'{WORKING_DIR}/lib', 'lib', False),
    ]
    assert select_root(roots, None) is roots[0]
    assert select_root(roots, 'lib') is roots[1]
    assert select_root(roots, 'x') is None
    assert select_root(roots, 'nope') is None


def test_roots_without_a_primary_start_at_the_working_folder():
    from openhands.app_server.app_conversation.additional_repositories import (
        conversation_roots,
        select_root,
    )

    roots = conversation_roots(WORKING_DIR, None, [_repo('org2/p/lib', 'lib')])

    assert [(r.path, r.folder) for r in roots] == [
        (WORKING_DIR, None),
        (f'{WORKING_DIR}/lib', 'lib'),
    ]
    assert select_root(roots, None).path == WORKING_DIR
    assert conversation_roots(WORKING_DIR, None, []) == [roots[0]]


def test_roots_of_a_single_repository_conversation_are_its_folder():
    """Invariant: one repository, one root, the project dir used before."""
    from openhands.app_server.app_conversation.additional_repositories import (
        conversation_roots,
    )

    roots = conversation_roots('/workspace', 'OpenHands/enterprise', [])

    assert [r.path for r in roots] == [
        get_project_dir('/workspace', 'OpenHands/enterprise')
    ]
    assert roots[0].primary is True


# --- /files ------------------------------------------------------------------


@pytest.mark.asyncio
class TestFilesPerRepository:
    async def _cwd(self, ctx, path, repository=None):
        client = _bash_client()
        with patch(
            ROUTER + '_get_agent_server_context', new=AsyncMock(return_value=ctx)
        ):
            await list_conversation_files(
                conversation_id=ctx.conversation.id,
                path=path,
                repository=repository,
                app_conversation_service=MagicMock(),
                sandbox_service=MagicMock(),
                sandbox_spec_service=MagicMock(),
                httpx_client=client,
            )
        return client.post.await_args.kwargs['json']['cwd']

    async def test_lists_an_additional_repository_with_the_selector(self):
        ctx = _ctx(additional=[_repo('org2/p/lib', 'lib')])

        assert await self._cwd(ctx, None, 'lib') == f'{WORKING_DIR}/lib'
        assert (
            await self._cwd(ctx, f'{WORKING_DIR}/lib/src', 'lib')
            == f'{WORKING_DIR}/lib/src'
        )
        # A path outside the selected repository falls back to its folder.
        assert await self._cwd(ctx, f'{WORKING_DIR}/app', 'lib') == f'{WORKING_DIR}/lib'

    async def test_another_repository_folder_needs_the_selector(self):
        """Without ``repository`` the listing stays inside the primary."""
        ctx = _ctx(additional=[_repo('org2/p/lib', 'lib')])

        assert await self._cwd(ctx, f'{WORKING_DIR}/lib', None) == f'{WORKING_DIR}/app'

    async def test_unknown_selector_is_404(self):
        ctx = _ctx(
            additional=[_repo('org2/p/lib', 'lib'), _repo('o/p/x', 'x', 'failed')]
        )

        for repository in ('nope', 'x', 'org2/p/lib'):
            with pytest.raises(HTTPException) as exc_info:
                await self._cwd(ctx, None, repository)
            assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND, repository

    async def test_without_a_primary_the_working_folder_is_the_default(self):
        ctx = _ctx(selected_repository=None, additional=[_repo('org2/p/lib', 'lib')])

        assert await self._cwd(ctx, None, None) == WORKING_DIR
        assert await self._cwd(ctx, f'{WORKING_DIR}/lib', None) == f'{WORKING_DIR}/lib'
        assert await self._cwd(ctx, None, 'lib') == f'{WORKING_DIR}/lib'

    async def test_single_repository_invariant(self):
        """One repository: the same folder as before, with or without the selector."""
        ctx = _ctx(selected_repository='OpenHands/enterprise', working_dir='/workspace')

        assert await self._cwd(ctx, None, None) == '/workspace/enterprise'
        assert (
            await self._cwd(ctx, '/workspace/project', None) == '/workspace/enterprise'
        )
        assert (
            await self._cwd(ctx, '/workspace/enterprise/frontend', None)
            == '/workspace/enterprise/frontend'
        )
        assert await self._cwd(ctx, None, 'enterprise') == '/workspace/enterprise'
        assert _resolve_workspace_dir(None, ctx) == get_project_dir(
            '/workspace', 'OpenHands/enterprise'
        )


# --- /file -------------------------------------------------------------------


class TestFilePathPerRepository:
    def test_relative_and_stale_paths_resolve_under_the_selected_repository(self):
        ctx = _ctx(
            additional=[_repo('org2/p/lib', 'lib', working_dir='/workspace')],
            working_dir='/workspace',
        )

        assert _resolve_file_path('README.md', ctx, 'lib') == '/workspace/lib/README.md'
        assert (
            _resolve_file_path('/workspace/lib/src/a.py', ctx, 'lib')
            == '/workspace/lib/src/a.py'
        )
        # The frontend's ``/workspace/project/<folder>`` convention.
        assert (
            _resolve_file_path('/workspace/project/lib/README.md', ctx, 'lib')
            == '/workspace/lib/README.md'
        )
        assert _resolve_file_path('', ctx, 'lib') == '/workspace/lib'

    def test_without_a_primary_paths_resolve_under_the_working_folder(self):
        ctx = _ctx(selected_repository=None, additional=[_repo('org2/p/lib', 'lib')])

        assert (
            _resolve_file_path('lib/README.md', ctx) == f'{WORKING_DIR}/lib/README.md'
        )
        assert (
            _resolve_file_path('README.md', ctx, 'lib')
            == f'{WORKING_DIR}/lib/README.md'
        )

    def test_single_repository_invariant(self):
        ctx = _ctx(selected_repository='OpenHands/enterprise', working_dir='/workspace')

        assert _resolve_file_path('README.md', ctx) == '/workspace/enterprise/README.md'
        assert (
            _resolve_file_path('/workspace/project/enterprise/README.md', ctx)
            == '/workspace/enterprise/README.md'
        )
        assert _resolve_file_path('/etc/passwd', ctx) == '/etc/passwd'
        assert (
            _resolve_file_path('README.md', ctx, 'enterprise')
            == '/workspace/enterprise/README.md'
        )

    def test_unknown_selector_is_404(self):
        ctx = _ctx(additional=[_repo('org2/p/lib', 'lib')])

        with pytest.raises(HTTPException) as exc_info:
            _resolve_file_path('README.md', ctx, 'nope')
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
class TestReadFilePerRepository:
    def _services(self, info, working_dir=WORKING_DIR):
        sandbox = MagicMock()
        sandbox.status = SandboxStatus.RUNNING
        sandbox.session_api_key = 'sess-key'
        exposed_url = MagicMock()
        exposed_url.name = AGENT_SERVER
        exposed_url.url = 'http://localhost:1234'
        sandbox.exposed_urls = [exposed_url]
        sandbox.sandbox_spec_id = 'spec-1'
        spec = MagicMock()
        spec.working_dir = working_dir
        app_service = MagicMock()
        app_service.get_app_conversation = AsyncMock(return_value=info)
        sb_service = MagicMock()
        sb_service.get_sandbox = AsyncMock(return_value=sandbox)
        spec_service = MagicMock()
        spec_service.resolve_existing_sandbox_spec = AsyncMock(return_value=spec)
        return app_service, sb_service, spec_service

    async def test_reads_a_file_of_the_additional_repository(self, tmp_path):
        info = _info(additional=[_repo('org2/p/lib', 'lib')])
        app_service, sb_service, spec_service = self._services(info)
        download_file = tmp_path / 'README.md'
        download_file.write_bytes(b'# lib')
        named_temp = MagicMock()
        named_temp.__enter__.return_value.name = str(download_file)
        named_temp.__exit__.return_value = False
        workspace = AsyncMock()
        workspace.file_download = AsyncMock(return_value=MagicMock(success=True))

        with (
            patch(ROUTER + 'AsyncRemoteWorkspace', return_value=workspace),
            patch(ROUTER + 'tempfile.NamedTemporaryFile', return_value=named_temp),
        ):
            result = await read_conversation_file(
                conversation_id=info.id,
                file_path='README.md',
                repository='lib',
                app_conversation_service=app_service,
                sandbox_service=sb_service,
                sandbox_spec_service=spec_service,
            )

        assert result == '# lib'
        call = workspace.file_download.await_args
        assert call.kwargs['source_path'] == f'{WORKING_DIR}/lib/README.md'

    async def test_unknown_selector_is_404(self):
        info = _info(additional=[_repo('org2/p/lib', 'lib')])
        app_service, sb_service, spec_service = self._services(info)

        with pytest.raises(HTTPException) as exc_info:
            await read_conversation_file(
                conversation_id=info.id,
                file_path='README.md',
                repository='nope',
                app_conversation_service=app_service,
                sandbox_service=sb_service,
                sandbox_spec_service=spec_service,
            )
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


# --- /git/changes and /git/diff ----------------------------------------------


@pytest.mark.asyncio
class TestGitPerRepository:
    async def _params(self, endpoint, ctx, path, repository=None):
        client = _get_client([])
        with patch(
            ROUTER + '_get_agent_server_context', new=AsyncMock(return_value=ctx)
        ):
            await endpoint(
                conversation_id=ctx.conversation.id,
                path=path,
                ref=None,
                repository=repository,
                app_conversation_service=MagicMock(),
                sandbox_service=MagicMock(),
                sandbox_spec_service=MagicMock(),
                httpx_client=client,
            )
        return client.get.await_args.kwargs['params']

    async def test_changes_with_the_selector_default_to_the_repository_folder(self):
        ctx = _ctx(additional=[_repo('org2/p/lib', 'lib')])

        params = await self._params(get_conversation_git_changes, ctx, None, 'lib')
        assert params == {'path': f'{WORKING_DIR}/lib'}
        params = await self._params(
            get_conversation_git_changes, ctx, f'{WORKING_DIR}/app', 'lib'
        )
        assert params == {'path': f'{WORKING_DIR}/lib'}

    async def test_changes_without_a_path_default_to_the_primary(self):
        ctx = _ctx(additional=[_repo('org2/p/lib', 'lib')])

        params = await self._params(get_conversation_git_changes, ctx, None)

        assert params == {'path': f'{WORKING_DIR}/app'}

    async def test_changes_without_a_primary_default_to_the_working_folder(self):
        ctx = _ctx(selected_repository=None, additional=[_repo('org2/p/lib', 'lib')])

        params = await self._params(get_conversation_git_changes, ctx, None)

        assert params == {'path': WORKING_DIR}

    async def test_diff_with_the_selector_resolves_the_file_in_that_repository(self):
        ctx = _ctx(additional=[_repo('org2/p/lib', 'lib')])

        params = await self._params(get_conversation_git_diff, ctx, 'src/a.py', 'lib')
        assert params == {'path': f'{WORKING_DIR}/lib/src/a.py'}
        params = await self._params(
            get_conversation_git_diff, ctx, f'{WORKING_DIR}/lib/src/a.py', 'lib'
        )
        assert params == {'path': f'{WORKING_DIR}/lib/src/a.py'}

    async def test_explicit_paths_pass_through_without_the_selector(self):
        """Invariant: today's callers send absolute paths and get them forwarded."""
        ctx = _ctx(additional=[_repo('org2/p/lib', 'lib')])

        for endpoint, path in (
            (get_conversation_git_changes, '/workspace/project'),
            (get_conversation_git_diff, '/workspace/project/file.py'),
        ):
            assert await self._params(endpoint, ctx, path) == {'path': path}

    async def test_unknown_selector_is_404(self):
        ctx = _ctx(additional=[_repo('org2/p/lib', 'lib')])

        for endpoint, path in (
            (get_conversation_git_changes, None),
            (get_conversation_git_diff, 'a.py'),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await self._params(endpoint, ctx, path, 'nope')
            assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


# --- /skills and /hooks ------------------------------------------------------


def _conversation(additional=(), selected_repository='org1/p/app', **kwargs):
    return AppConversation(
        id=uuid4(),
        created_by_user_id='u',
        sandbox_id=str(uuid4()),
        selected_repository=selected_repository,
        git_provider=ADO if selected_repository else None,
        additional_repositories=list(additional),
        sandbox_status=SandboxStatus.RUNNING,
        **kwargs,
    )


def _sandbox_services(conversation):
    sandbox = SandboxInfo(
        id=conversation.sandbox_id,
        created_by_user_id='u',
        status=SandboxStatus.RUNNING,
        sandbox_spec_id=str(uuid4()),
        session_api_key='sess-key',
        exposed_urls=[
            ExposedUrl(name=AGENT_SERVER, url='http://agent-server:8000', port=8000)
        ],
    )
    spec = SandboxSpecInfo(id=str(uuid4()), command=None, working_dir=WORKING_DIR)
    sandbox_service = MagicMock()
    sandbox_service.get_sandbox = AsyncMock(return_value=sandbox)
    spec_service = MagicMock()
    spec_service.resolve_existing_sandbox_spec = AsyncMock(return_value=spec)
    return sandbox_service, spec_service


def _skills_service(conversation, skills):
    mock_cls = type('AppConversationServiceMock', (MagicMock,), {})
    AppConversationServiceBase.register(mock_cls)
    service = mock_cls()
    service.user_context = MagicMock(spec=UserContext)
    service.get_app_conversation = AsyncMock(return_value=conversation)
    service.load_and_merge_all_skills = AsyncMock(return_value=skills)
    return service


@pytest.mark.asyncio
class TestSkillsPerRepository:
    async def _names(self, conversation, skills, repository=None):
        service = _skills_service(conversation, skills)
        sandbox_service, spec_service = _sandbox_services(conversation)
        response = await get_conversation_skills(
            conversation_id=conversation.id,
            repository=repository,
            app_conversation_service=service,
            sandbox_service=sandbox_service,
            sandbox_spec_service=spec_service,
        )
        assert response.status_code == status.HTTP_200_OK
        data = json.loads(response.body.decode('utf-8'))
        return [s['name'] for s in data['skills']], service

    async def test_lists_every_repository_and_filters_by_selector(self):
        lib = _repo('org2/p/lib', 'lib')
        conversation = _conversation(additional=[lib])
        skills = [
            Skill(name='agents', content='a', source=f'{WORKING_DIR}/app/AGENTS.md'),
            Skill(
                name='agents:lib', content='b', source=f'{WORKING_DIR}/lib/AGENTS.md'
            ),
            Skill(name='public', content='c', source='/root/.openhands/skills/p.md'),
        ]

        names, service = await self._names(conversation, skills)
        assert names == ['agents', 'agents:lib', 'public']
        kwargs = service.load_and_merge_all_skills.await_args.kwargs
        assert kwargs['additional_repositories'] == [lib]

        names, _ = await self._names(conversation, skills, 'lib')
        assert names == ['agents:lib']
        names, _ = await self._names(conversation, skills, 'app')
        assert names == ['agents']

    async def test_unknown_selector_is_404_not_500(self):
        conversation = _conversation(additional=[_repo('org2/p/lib', 'lib')])
        service = _skills_service(conversation, [])
        sandbox_service, spec_service = _sandbox_services(conversation)

        with pytest.raises(HTTPException) as exc_info:
            await get_conversation_skills(
                conversation_id=conversation.id,
                repository='nope',
                app_conversation_service=service,
                sandbox_service=sandbox_service,
                sandbox_spec_service=spec_service,
            )
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        service.load_and_merge_all_skills.assert_not_awaited()

    async def test_single_repository_invariant(self):
        """One repository: the loader gets no additional repositories."""
        conversation = _conversation()
        skills = [
            Skill(name='agents', content='a', source=f'{WORKING_DIR}/app/AGENTS.md')
        ]

        names, service = await self._names(conversation, skills)

        assert names == ['agents']
        kwargs = service.load_and_merge_all_skills.await_args.kwargs
        assert kwargs['additional_repositories'] == []
        assert service.load_and_merge_all_skills.await_args.args[2] == (
            f'{WORKING_DIR}/app'
        )


@pytest.mark.asyncio
class TestHooksPerRepository:
    async def _project_dir(self, conversation, repository=None):
        service = MagicMock()
        service.get_app_conversation = AsyncMock(return_value=conversation)
        sandbox_service, spec_service = _sandbox_services(conversation)
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json.return_value = {'hook_config': {}}
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=response)
        result = await get_conversation_hooks(
            conversation_id=conversation.id,
            repository=repository,
            app_conversation_service=service,
            sandbox_service=sandbox_service,
            sandbox_spec_service=spec_service,
            httpx_client=client,
        )
        assert result.status_code == status.HTTP_200_OK
        return client.post.await_args.kwargs['json']['project_dir']

    async def test_reads_the_primary_by_default_and_the_selected_folder(self):
        conversation = _conversation(additional=[_repo('org2/p/lib', 'lib')])

        assert await self._project_dir(conversation) == f'{WORKING_DIR}/app'
        assert await self._project_dir(conversation, 'lib') == f'{WORKING_DIR}/lib'
        assert await self._project_dir(_conversation()) == f'{WORKING_DIR}/app'
        assert await self._project_dir(_conversation(selected_repository=None)) == (
            WORKING_DIR
        )

    async def test_unknown_selector_is_404_not_500(self):
        conversation = _conversation(additional=[_repo('org2/p/lib', 'lib')])

        with pytest.raises(HTTPException) as exc_info:
            await self._project_dir(conversation, 'nope')
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
