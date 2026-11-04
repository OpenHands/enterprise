"""Tests for consistent cwd and plan-artifact resolution across grouped/provider workspaces.

Covers OSS→OHE parity (Enterprise issue #849): the launch-resolved working
directory (pinned in the ``archiveworkspacepath`` tag) and the provider-aware
PLAN.md path are the authoritative roots for the Files tab, file reads, and the
Preview→Build plan contract — never reconstructed from the sandbox-spec base
plus a repo name.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

from openhands.app_server.app_conversation.app_conversation_models import (
    ARCHIVE_WORKSPACE_PATH_TAG_KEY,
    AppConversationInfo,
)
from openhands.app_server.app_conversation.app_conversation_router import (
    AGENT_SERVER,
    AgentServerContext,
    ConversationPlanArtifact,
    _conversation_roots,
    _conversation_working_dir,
    _resolve_file_path,
    _select_root,
    compute_plan_path,
    conversation_roots,
    get_conversation_plan,
    list_conversation_files,
    read_conversation_file,
)
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.sandbox.sandbox_models import SandboxStatus


def _make_agent_server_context(
    conversation_id,
    working_dir: str = '/workspace/project',
    selected_repository: str | None = None,
    git_provider: ProviderType | None = None,
    archive_workspace_path: str | None = None,
    additional_repositories=None,
) -> AgentServerContext:
    """Build a minimal AgentServerContext with a real ``tags`` dict."""
    tags: dict[str, str] = {}
    if archive_workspace_path is not None:
        tags[ARCHIVE_WORKSPACE_PATH_TAG_KEY] = archive_workspace_path
    info = AppConversationInfo(
        id=conversation_id,
        created_by_user_id='test-user',
        sandbox_id=str(uuid4()),
        llm_model='openai/old-model',
        selected_repository=selected_repository,
        git_provider=git_provider,
        additional_repositories=additional_repositories or [],
        tags=tags,
    )
    sandbox_spec = MagicMock()
    sandbox_spec.working_dir = working_dir
    return AgentServerContext(
        conversation=info,
        sandbox=MagicMock(status=SandboxStatus.RUNNING),
        sandbox_spec=sandbox_spec,
        agent_server_url='http://agent.test',
        session_api_key='sess-key',
    )


def _bash_response(stdout='', exit_code=0):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(
        return_value={'exit_code': exit_code, 'stdout': stdout, 'stderr': ''}
    )
    return response


def _httpx_client(response=None):
    client = AsyncMock()
    client.post = AsyncMock(return_value=response or _bash_response(''))
    return client


class TestGroupedWorkspaceRootResolution:
    """Resolve cwd from the launch-pinned working dir, not the sandbox-spec base."""

    @pytest.mark.asyncio
    async def test_list_uses_pinned_grouped_working_dir(self):
        conv_id = uuid4()
        ctx = _make_agent_server_context(
            conv_id,
            working_dir='/workspace/project',
            selected_repository='OpenHands/enterprise',
            archive_workspace_path=f'/workspace/project/{conv_id.hex}',
        )
        client = _httpx_client()
        with patch(
            'openhands.app_server.app_conversation.app_conversation_router.'
            '_get_agent_server_context',
            new=AsyncMock(return_value=ctx),
        ):
            await list_conversation_files(
                conversation_id=conv_id,
                path=None,
                app_conversation_service=MagicMock(),
                sandbox_service=MagicMock(),
                sandbox_spec_service=MagicMock(),
                httpx_client=client,
            )
        assert client.post.await_args.kwargs['json']['cwd'] == (
            f'/workspace/project/{conv_id.hex}/enterprise'
        )

    @pytest.mark.asyncio
    async def test_list_without_repository_uses_pinned_working_dir(self):
        conv_id = uuid4()
        ctx = _make_agent_server_context(
            conv_id,
            working_dir='/workspace/project',
            selected_repository=None,
            archive_workspace_path=f'/workspace/project/{conv_id.hex}',
        )
        client = _httpx_client()
        with patch(
            'openhands.app_server.app_conversation.app_conversation_router.'
            '_get_agent_server_context',
            new=AsyncMock(return_value=ctx),
        ):
            await list_conversation_files(
                conversation_id=conv_id,
                path=None,
                app_conversation_service=MagicMock(),
                sandbox_service=MagicMock(),
                sandbox_spec_service=MagicMock(),
                httpx_client=client,
            )
        assert client.post.await_args.kwargs['json']['cwd'] == (
            f'/workspace/project/{conv_id.hex}'
        )

    def test_select_root_prefers_pinned_working_dir(self):
        conv_id = uuid4()
        ctx = _make_agent_server_context(
            conv_id,
            working_dir='/workspace/project',
            selected_repository='OpenHands/enterprise',
            archive_workspace_path=f'/workspace/project/{conv_id.hex}',
        )
        root = _select_root(ctx, None)
        assert root.path == f'/workspace/project/{conv_id.hex}/enterprise'
        assert root.folder == 'enterprise'
        assert root.primary is True

    def test_select_root_falls_back_to_spec_working_dir_without_tag(self):
        conv_id = uuid4()
        ctx = _make_agent_server_context(
            conv_id,
            working_dir='/workspace/project',
            selected_repository='OpenHands/enterprise',
        )
        root = _select_root(ctx, None)
        assert root.path == '/workspace/project/enterprise'

    def test_conversation_working_dir_reads_archive_tag(self):
        conv_id = uuid4()
        ctx = _make_agent_server_context(
            conv_id,
            working_dir='/workspace/project',
            archive_workspace_path='/workspace/project/abc123',
        )
        assert _conversation_working_dir(ctx) == '/workspace/project/abc123'

    def test_resolve_file_path_anchors_on_grouped_root(self):
        conv_id = uuid4()
        ctx = _make_agent_server_context(
            conv_id,
            working_dir='/workspace/project',
            selected_repository='OpenHands/enterprise',
            archive_workspace_path=f'/workspace/project/{conv_id.hex}',
        )
        # A path already inside the grouped root is used verbatim (no
        # re-anchoring, no double-prefix).
        grouped_root = f'/workspace/project/{conv_id.hex}/enterprise'
        assert _resolve_file_path(f'{grouped_root}/README.md', ctx) == (
            f'{grouped_root}/README.md'
        )

    def test_resolve_file_path_remaps_stale_default_under_grouped_root(self):
        """A NO_GROUPING-style frontend path is re-anchored under the grouped
        cwd: the stale default ``/workspace/project/enterprise`` is rewritten to
        the runtime's grouped clone location, preserving the relative path."""
        conv_id = uuid4()
        ctx = _make_agent_server_context(
            conv_id,
            working_dir='/workspace',  # sandbox-spec base (NO_GROUPING convention)
            selected_repository='OpenHands/enterprise',
            archive_workspace_path=f'/workspace/project/{conv_id.hex}',
        )
        assert (
            _resolve_file_path('/workspace/project/enterprise/README.md', ctx)
            == f'/workspace/project/{conv_id.hex}/enterprise/README.md'
        )

    def test_roots_match_across_grouping_strategies(self):
        """NO_GROUPING and a grouped strategy serve the same folder name and
        primary flag; only the absolute path differs by the grouped cwd prefix,
        which is exactly the launch invariant the parity work enforces."""
        conv_id = uuid4()
        no_group = _make_agent_server_context(
            conv_id,
            working_dir='/workspace/project',
            selected_repository='OpenHands/enterprise',
            archive_workspace_path='/workspace/project',
        )
        grouped = _make_agent_server_context(
            conv_id,
            working_dir='/workspace/project',
            selected_repository='OpenHands/enterprise',
            archive_workspace_path=f'/workspace/project/{conv_id.hex}',
        )
        ng = _conversation_roots(no_group)[0]
        g = _conversation_roots(grouped)[0]
        assert ng.folder == g.folder == 'enterprise'
        assert ng.primary and g.primary
        assert ng.path == '/workspace/project/enterprise'
        assert g.path == f'/workspace/project/{conv_id.hex}/enterprise'


class TestWorkspaceContainment:
    """Root fences are never loosened to accommodate path guesses."""

    @pytest.mark.asyncio
    async def test_parent_traversal_path_is_rejected_for_listing(self):
        conv_id = uuid4()
        ctx = _make_agent_server_context(
            conv_id,
            working_dir='/workspace/project',
            selected_repository='OpenHands/enterprise',
            archive_workspace_path=f'/workspace/project/{conv_id.hex}',
        )
        client = _httpx_client()
        with patch(
            'openhands.app_server.app_conversation.app_conversation_router.'
            '_get_agent_server_context',
            new=AsyncMock(return_value=ctx),
        ):
            await list_conversation_files(
                conversation_id=conv_id,
                path=f'/workspace/project/{conv_id.hex}/enterprise/../other-repo',
                app_conversation_service=MagicMock(),
                sandbox_service=MagicMock(),
                sandbox_spec_service=MagicMock(),
                httpx_client=client,
            )
        assert client.post.await_args.kwargs['json']['cwd'] == (
            f'/workspace/project/{conv_id.hex}/enterprise'
        )

    @pytest.mark.asyncio
    async def test_sibling_repository_is_not_listable_without_repository_param(self):
        conv_id = uuid4()
        ctx = _make_agent_server_context(
            conv_id,
            working_dir='/workspace/project',
            selected_repository='OpenHands/enterprise',
            archive_workspace_path=f'/workspace/project/{conv_id.hex}',
        )
        client = _httpx_client()
        with patch(
            'openhands.app_server.app_conversation.app_conversation_router.'
            '_get_agent_server_context',
            new=AsyncMock(return_value=ctx),
        ):
            await list_conversation_files(
                conversation_id=conv_id,
                path=f'/workspace/project/{conv_id.hex}/other-repo',
                app_conversation_service=MagicMock(),
                sandbox_service=MagicMock(),
                sandbox_spec_service=MagicMock(),
                httpx_client=client,
            )
        assert client.post.await_args.kwargs['json']['cwd'] == (
            f'/workspace/project/{conv_id.hex}/enterprise'
        )

    def test_unknown_repository_folder_is_404(self):
        conv_id = uuid4()
        ctx = _make_agent_server_context(
            conv_id,
            working_dir='/workspace/project',
            selected_repository='OpenHands/enterprise',
            archive_workspace_path=f'/workspace/project/{conv_id.hex}',
        )
        with pytest.raises(HTTPException) as exc_info:
            _select_root(ctx, 'not-a-cloned-folder')
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    def test_resolve_file_path_rejects_escape_outside_root(self):
        conv_id = uuid4()
        ctx = _make_agent_server_context(
            conv_id,
            working_dir='/workspace/project',
            selected_repository='OpenHands/enterprise',
            archive_workspace_path=f'/workspace/project/{conv_id.hex}',
        )
        # /etc/passwd is under no recognized root → passthrough (runtime 404).
        assert _resolve_file_path('/etc/passwd', ctx) == '/etc/passwd'

    def test_no_sibling_access_across_conversations(self):
        """Two co-located conversations resolve distinct roots; neither can name
        the other's folder and read it through ``repository``."""
        conv_a = uuid4()
        conv_b = uuid4()
        ctx_a = _make_agent_server_context(
            conv_a,
            working_dir='/workspace/project',
            selected_repository='OpenHands/a',
            archive_workspace_path=f'/workspace/project/{conv_a.hex}',
        )
        ctx_b = _make_agent_server_context(
            conv_b,
            working_dir='/workspace/project',
            selected_repository='OpenHands/b',
            archive_workspace_path=f'/workspace/project/{conv_b.hex}',
        )
        root_a = _select_root(ctx_a, None)
        root_b = _select_root(ctx_b, None)
        assert root_a.path != root_b.path
        # Conversation A cannot select B's folder.
        with pytest.raises(HTTPException) as exc_info:
            _select_root(ctx_a, root_b.folder)
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


class TestComputePlanPath:
    """The shared plan-path resolver is provider-aware and anchor-correct."""

    def test_default_provider_uses_agents_tmp(self):
        assert compute_plan_path('/workspace/project', None) == (
            '/workspace/project/.agents_tmp/PLAN.md'
        )

    def test_github_uses_agents_tmp(self):
        assert compute_plan_path('/workspace/project', ProviderType.GITHUB) == (
            '/workspace/project/.agents_tmp/PLAN.md'
        )

    def test_gitlab_uses_agents_tmp_config(self):
        assert compute_plan_path('/workspace/project', ProviderType.GITLAB) == (
            '/workspace/project/agents-tmp-config/PLAN.md'
        )

    def test_azure_devops_uses_agents_tmp_config(self):
        assert compute_plan_path('/workspace/project', ProviderType.AZURE_DEVOPS) == (
            '/workspace/project/agents-tmp-config/PLAN.md'
        )

    def test_anchored_on_grouped_working_dir(self):
        grouped = '/workspace/project/abc123/enterprise'
        assert compute_plan_path(grouped, None) == (
            '/workspace/project/abc123/enterprise/.agents_tmp/PLAN.md'
        )

    def test_matches_launch_compute(self):
        """The router's resolver and the launch service agree on the path."""
        from openhands.app_server.app_conversation.live_status_app_conversation_service import (
            LiveStatusAppConversationService,
        )

        service = LiveStatusAppConversationService.__new__(
            LiveStatusAppConversationService
        )
        project_dir = '/workspace/project/abc123/enterprise'
        for provider in (
            None,
            ProviderType.GITHUB,
            ProviderType.GITLAB,
            ProviderType.AZURE_DEVOPS,
        ):
            assert service._compute_plan_path(project_dir, provider) == (
                compute_plan_path(project_dir, provider)
            )


@pytest.mark.asyncio
class TestGetConversationPlan:
    """The typed ``/plan`` contract resolves and reads the plan artifact."""

    def _make_ctx(self, conv_id, git_provider=None, archive_workspace_path=None):
        return _make_agent_server_context(
            conv_id,
            working_dir='/workspace/project',
            selected_repository='OpenHands/enterprise',
            git_provider=git_provider,
            archive_workspace_path=archive_workspace_path,
        )

    async def test_returns_resolved_path_and_content(self, tmp_path):
        conv_id = uuid4()
        ctx = self._make_ctx(
            conv_id,
            git_provider=ProviderType.GITHUB,
            archive_workspace_path=f'/workspace/project/{conv_id.hex}',
        )
        plan_file = tmp_path / 'PLAN.md'
        plan_file.write_bytes(b'# Plan\n- step 1')

        workspace = AsyncMock()
        workspace.file_download = AsyncMock(return_value=MagicMock(success=True))
        named_temp = MagicMock()
        named_temp.__enter__.return_value.name = str(plan_file)
        named_temp.__exit__.return_value = False

        with (
            patch(
                'openhands.app_server.app_conversation.app_conversation_router.'
                '_get_agent_server_context',
                new=AsyncMock(return_value=ctx),
            ),
            patch(
                'openhands.app_server.app_conversation.app_conversation_router.'
                'AsyncRemoteWorkspace',
                return_value=workspace,
            ),
            patch(
                'openhands.app_server.app_conversation.app_conversation_router.'
                'tempfile.NamedTemporaryFile',
                return_value=named_temp,
            ),
        ):
            result = await get_conversation_plan(
                conversation_id=conv_id,
                app_conversation_service=MagicMock(),
                sandbox_service=MagicMock(),
                sandbox_spec_service=MagicMock(),
            )

        assert isinstance(result, ConversationPlanArtifact)
        assert result.exists is True
        assert result.content == '# Plan\n- step 1'
        assert result.path == (
            f'/workspace/project/{conv_id.hex}/enterprise/.agents_tmp/PLAN.md'
        )
        assert workspace.file_download.await_args.kwargs['source_path'] == result.path

    async def test_missing_plan_reports_not_existing(self):
        conv_id = uuid4()
        ctx = self._make_ctx(
            conv_id,
            git_provider=ProviderType.GITLAB,
            archive_workspace_path=f'/workspace/project/{conv_id.hex}',
        )
        workspace = AsyncMock()
        workspace.file_download = AsyncMock(return_value=MagicMock(success=False))
        named_temp = MagicMock()
        named_temp.__enter__.return_value.name = '/tmp/none'
        named_temp.__exit__.return_value = False

        with (
            patch(
                'openhands.app_server.app_conversation.app_conversation_router.'
                '_get_agent_server_context',
                new=AsyncMock(return_value=ctx),
            ),
            patch(
                'openhands.app_server.app_conversation.app_conversation_router.'
                'AsyncRemoteWorkspace',
                return_value=workspace,
            ),
            patch(
                'openhands.app_server.app_conversation.app_conversation_router.'
                'tempfile.NamedTemporaryFile',
                return_value=named_temp,
            ),
        ):
            result = await get_conversation_plan(
                conversation_id=conv_id,
                app_conversation_service=MagicMock(),
                sandbox_service=MagicMock(),
                sandbox_spec_service=MagicMock(),
            )

        assert result.exists is False
        assert result.content == ''
        assert result.path == (
            f'/workspace/project/{conv_id.hex}/enterprise/agents-tmp-config/PLAN.md'
        )

    async def test_paused_sandbox_is_409(self):
        with patch(
            'openhands.app_server.app_conversation.app_conversation_router.'
            '_get_agent_server_context',
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_conversation_plan(
                    conversation_id=uuid4(),
                    app_conversation_service=MagicMock(),
                    sandbox_service=MagicMock(),
                    sandbox_spec_service=MagicMock(),
                )
        assert exc_info.value.status_code == status.HTTP_409_CONFLICT

    async def test_unreachable_conversation_is_404(self):
        not_found = JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND, content={'error': 'nope'}
        )
        with patch(
            'openhands.app_server.app_conversation.app_conversation_router.'
            '_get_agent_server_context',
            new=AsyncMock(return_value=not_found),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_conversation_plan(
                    conversation_id=uuid4(),
                    app_conversation_service=MagicMock(),
                    sandbox_service=MagicMock(),
                    sandbox_spec_service=MagicMock(),
                )
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    async def test_read_error_is_not_existing(self, tmp_path):
        """A runtime exception surfaces as ``exists=False``, not a 500."""
        conv_id = uuid4()
        ctx = self._make_ctx(
            conv_id,
            git_provider=ProviderType.AZURE_DEVOPS,
            archive_workspace_path=f'/workspace/project/{conv_id.hex}',
        )
        workspace = AsyncMock()
        workspace.file_download = AsyncMock(side_effect=RuntimeError('boom'))
        named_temp = MagicMock()
        named_temp.__enter__.return_value.name = str(tmp_path / 'x')
        named_temp.__exit__.return_value = False

        with (
            patch(
                'openhands.app_server.app_conversation.app_conversation_router.'
                '_get_agent_server_context',
                new=AsyncMock(return_value=ctx),
            ),
            patch(
                'openhands.app_server.app_conversation.app_conversation_router.'
                'AsyncRemoteWorkspace',
                return_value=workspace,
            ),
            patch(
                'openhands.app_server.app_conversation.app_conversation_router.'
                'tempfile.NamedTemporaryFile',
                return_value=named_temp,
            ),
        ):
            result = await get_conversation_plan(
                conversation_id=conv_id,
                app_conversation_service=MagicMock(),
                sandbox_service=MagicMock(),
                sandbox_spec_service=MagicMock(),
            )

        assert result.exists is False
        assert result.content == ''


@pytest.mark.asyncio
class TestReadConversationFileDefaultPlan:
    """With no ``file_path`` the /file endpoint reads the resolved plan artifact."""

    async def test_default_file_path_is_resolved_plan(self, tmp_path):
        conv_id = uuid4()
        conv = MagicMock()
        conv.id = conv_id
        conv.sandbox_id = 'sandbox-1'
        conv.selected_repository = 'OpenHands/enterprise'
        conv.git_provider = ProviderType.AZURE_DEVOPS
        conv.tags = {
            ARCHIVE_WORKSPACE_PATH_TAG_KEY: f'/workspace/project/{conv_id.hex}'
        }
        conv.additional_repositories = []
        sandbox = MagicMock()
        sandbox.status = SandboxStatus.RUNNING
        sandbox.session_api_key = 'sess-key'
        exposed_url = MagicMock()
        exposed_url.name = AGENT_SERVER
        exposed_url.url = 'http://localhost:1234'
        sandbox.exposed_urls = [exposed_url]
        sandbox.sandbox_spec_id = 'spec-1'
        sandbox_spec = MagicMock()
        sandbox_spec.working_dir = '/workspace/project'

        app_service = MagicMock()
        app_service.get_app_conversation = AsyncMock(return_value=conv)
        sb_service = MagicMock()
        sb_service.get_sandbox = AsyncMock(return_value=sandbox)
        spec_service = MagicMock()
        spec_service.resolve_existing_sandbox_spec = AsyncMock(
            return_value=sandbox_spec
        )

        plan_file = tmp_path / 'PLAN.md'
        plan_file.write_bytes(b'# the plan')
        named_temp = MagicMock()
        named_temp.__enter__.return_value.name = str(plan_file)
        named_temp.__exit__.return_value = False

        workspace = AsyncMock()
        workspace.file_download = AsyncMock(return_value=MagicMock(success=True))

        with (
            patch(
                'openhands.app_server.app_conversation.app_conversation_router.'
                'AsyncRemoteWorkspace',
                return_value=workspace,
            ),
            patch(
                'openhands.app_server.app_conversation.app_conversation_router.'
                'tempfile.NamedTemporaryFile',
                return_value=named_temp,
            ),
            patch(
                'openhands.app_server.app_conversation.app_conversation_router.'
                'replace_localhost_hostname_for_docker',
                side_effect=lambda u: u,
            ),
        ):
            result = await read_conversation_file(
                conversation_id=conv_id,
                file_path='',
                app_conversation_service=app_service,
                sandbox_service=sb_service,
                sandbox_spec_service=spec_service,
            )

        assert result == '# the plan'
        call = workspace.file_download.await_args
        # Azure DevOps + grouped cwd → agents-tmp-config under the pinned root.
        assert call.kwargs['source_path'] == (
            f'/workspace/project/{conv_id.hex}/enterprise/agents-tmp-config/PLAN.md'
        )


class TestConversationRootsDoc:
    """Sanity checks for the shared roots builder used by the router."""

    def test_primary_first_then_cloned_additionals(self):
        from openhands.app_server.app_conversation.additional_repositories import (
            AdditionalRepository,
            AdditionalRepositoryStatus,
        )

        additionals = [
            AdditionalRepository(
                repository='OpenHands/sdk',
                git_provider=ProviderType.GITHUB,
                folder='sdk',
                path='/workspace/project/sdk',
                status=AdditionalRepositoryStatus.CLONED,
            ),
            AdditionalRepository(
                repository='OpenHands/broken',
                git_provider=ProviderType.GITHUB,
                folder='broken',
                path='/workspace/project/broken',
                status=AdditionalRepositoryStatus.FAILED,
            ),
        ]
        roots = conversation_roots(
            '/workspace/project',
            'OpenHands/enterprise',
            additionals,
        )
        assert [r.folder for r in roots] == ['enterprise', 'sdk']
        assert roots[0].primary is True
        assert roots[1].primary is False
