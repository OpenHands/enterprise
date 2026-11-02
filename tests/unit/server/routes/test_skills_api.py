import json
import os
import subprocess
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.app_server.app import app
from openhands.app_server.file_store.memory import InMemoryFileStore
from openhands.app_server.integrations.provider import (
    ProviderHandler,
    ProviderToken,
    ProviderType,
)
from openhands.app_server.integrations.service_types import Repository, UserGitInfo
from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.secrets.secrets_store import SecretsStore
from openhands.app_server.settings.file_settings_store import FileSettingsStore
from openhands.app_server.settings.settings_models import MarketplaceRegistration
from openhands.app_server.settings.settings_store import SettingsStore
from openhands.app_server.user.skills_router import (
    GLOBAL_SKILLS_DIR,
    MarketplacePluginPreview,
    MarketplaceSkillsPreviewResponse,
    _bearer_auth_git_env,
    _clone_marketplace_repo,
    check_marketplace,
    get_marketplace_skills,
)
from openhands.app_server.user_auth.user_auth import UserAuth


class MockUserAuth(UserAuth):
    """Mock implementation of UserAuth for testing."""

    def __init__(self):
        self._settings = None
        self._settings_store = MagicMock()
        self._settings_store.load = AsyncMock(return_value=None)
        self._settings_store.store = AsyncMock()

    async def get_user_id(self) -> str | None:
        return 'test-user'

    async def get_user_email(self) -> str | None:
        return 'test-email@whatever.com'

    async def get_access_token(self) -> SecretStr | None:
        return SecretStr('test-token')

    async def get_provider_tokens(
        self,
    ) -> dict[ProviderType, ProviderToken] | None:
        return None

    async def get_user_settings_store(self) -> SettingsStore | None:
        return self._settings_store

    async def get_secrets_store(self) -> SecretsStore | None:
        return None

    async def get_secrets(self) -> Secrets | None:
        return None

    async def get_mcp_api_key(self) -> str | None:
        return None

    async def get_user_git_info(self) -> UserGitInfo | None:
        return None

    @classmethod
    async def get_instance(cls, request: Request) -> UserAuth:
        return MockUserAuth()

    @classmethod
    async def get_for_user(cls, user_id: str) -> UserAuth:
        return MockUserAuth()


@pytest.fixture
def test_client():
    with (
        patch.dict(os.environ, {'SESSION_API_KEY': ''}, clear=False),
        patch('openhands.app_server.utils.dependencies._SESSION_API_KEY', None),
        patch(
            'openhands.app_server.user_auth.user_auth.UserAuth.get_instance',
            return_value=MockUserAuth(),
        ),
        patch(
            'openhands.app_server.settings.file_settings_store.FileSettingsStore.get_instance',
            AsyncMock(return_value=FileSettingsStore(InMemoryFileStore())),
        ),
    ):
        client = TestClient(app)
        yield client


def _write_skill_file(
    dir_path: Path,
    name: str,
    skill_type: str = 'knowledge',
    triggers: list[str] | None = None,
    description: str | None = None,
) -> None:
    """Write a mock skill markdown file with frontmatter."""
    dir_path.mkdir(parents=True, exist_ok=True)
    lines = [
        '---',
        f'name: {name}',
        f'type: {skill_type}',
    ]
    if description:
        lines.append(f'description: {description}')
    if triggers:
        lines.append('triggers:')
        for t in triggers:
            lines.append(f'- {t}')
    lines.append('---')
    lines.append(f'{name} content')
    (dir_path / f'{name}.md').write_text('\n'.join(lines))


@pytest.mark.asyncio
async def test_skills_search_returns_skills(test_client, tmp_path):
    """Test that GET /api/v1/skills/search returns a paginated list of skills."""
    global_dir = tmp_path / 'global'
    _write_skill_file(global_dir, 'test_repo', skill_type='repo')
    _write_skill_file(
        global_dir, 'test_knowledge', skill_type='knowledge', triggers=['testword']
    )

    with (
        patch('openhands.app_server.user.skills_router.GLOBAL_SKILLS_DIR', global_dir),
        patch(
            'openhands.app_server.user.skills_router.USER_SKILLS_DIR',
            tmp_path / 'nonexistent',
        ),
    ):
        response = test_client.get('/api/v1/skills/search')

    assert response.status_code == 200
    data = response.json()
    assert 'items' in data
    assert 'next_page_id' in data
    assert len(data['items']) == 2

    # Verify skill structure
    skill_names = [s['name'] for s in data['items']]
    assert 'test_repo' in skill_names
    assert 'test_knowledge' in skill_names

    # Check knowledge skill has triggers
    knowledge_skill = next(s for s in data['items'] if s['name'] == 'test_knowledge')
    assert knowledge_skill['triggers'] == ['testword']
    assert knowledge_skill['type'] == 'knowledge'

    # Check repo skill has no triggers
    repo_skill = next(s for s in data['items'] if s['name'] == 'test_repo')
    assert repo_skill['triggers'] is None
    assert repo_skill['type'] == 'repo'

    # No next page when all results fit
    assert data['next_page_id'] is None


@pytest.mark.asyncio
async def test_skills_search_handles_missing_dirs(test_client, tmp_path):
    """Test that the endpoint handles missing directories gracefully."""
    with (
        patch(
            'openhands.app_server.user.skills_router.GLOBAL_SKILLS_DIR',
            tmp_path / 'no_such_dir',
        ),
        patch(
            'openhands.app_server.user.skills_router.USER_SKILLS_DIR',
            tmp_path / 'also_missing',
        ),
    ):
        response = test_client.get('/api/v1/skills/search')

    assert response.status_code == 200
    data = response.json()
    assert data['items'] == []
    assert data['next_page_id'] is None


@pytest.mark.asyncio
async def test_skills_search_sorted_by_source_then_name(test_client, tmp_path):
    """Test that skills are sorted by source (global first) then by name."""
    global_dir = tmp_path / 'global'
    user_dir = tmp_path / 'user'

    _write_skill_file(global_dir, 'z_global', skill_type='repo')
    _write_skill_file(global_dir, 'a_global', skill_type='repo')
    _write_skill_file(user_dir, 'b_user', skill_type='repo')

    with (
        patch('openhands.app_server.user.skills_router.GLOBAL_SKILLS_DIR', global_dir),
        patch('openhands.app_server.user.skills_router.USER_SKILLS_DIR', user_dir),
    ):
        response = test_client.get('/api/v1/skills/search')

    assert response.status_code == 200
    data = response.json()
    skills = data['items']

    # Global skills should come first, sorted by name
    assert skills[0]['name'] == 'a_global'
    assert skills[0]['source'] == 'global'
    assert skills[1]['name'] == 'z_global'
    assert skills[1]['source'] == 'global'
    # User skills should come last
    assert skills[2]['name'] == 'b_user'
    assert skills[2]['source'] == 'user'


@pytest.mark.asyncio
async def test_skills_search_pagination(test_client, tmp_path):
    """Test cursor-based pagination."""
    global_dir = tmp_path / 'global'
    _write_skill_file(global_dir, 'skill_a', skill_type='repo')
    _write_skill_file(global_dir, 'skill_b', skill_type='repo')
    _write_skill_file(global_dir, 'skill_c', skill_type='repo')

    with (
        patch('openhands.app_server.user.skills_router.GLOBAL_SKILLS_DIR', global_dir),
        patch(
            'openhands.app_server.user.skills_router.USER_SKILLS_DIR',
            tmp_path / 'nonexistent',
        ),
    ):
        # First page with limit=2
        response = test_client.get('/api/v1/skills/search', params={'limit': 2})
        assert response.status_code == 200
        data = response.json()
        assert len(data['items']) == 2
        assert data['items'][0]['name'] == 'skill_a'
        assert data['items'][1]['name'] == 'skill_b'
        assert data['next_page_id'] == 'skill_b'

        # Second page using next_page_id
        response = test_client.get(
            '/api/v1/skills/search',
            params={'limit': 2, 'page_id': data['next_page_id']},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data['items']) == 1
        assert data['items'][0]['name'] == 'skill_c'
        assert data['next_page_id'] is None


def test_global_skills_dir_points_to_repo_root():
    """Test that GLOBAL_SKILLS_DIR points to the correct location.

    This test validates that GLOBAL_SKILLS_DIR is correctly configured to point
    to the skills/ directory at the repo root. This prevents regressions like the
    one introduced in fb98faf4a where an incorrect path caused no skills to load.
    """
    # The directory should exist
    assert GLOBAL_SKILLS_DIR.exists(), (
        f'GLOBAL_SKILLS_DIR does not exist: {GLOBAL_SKILLS_DIR}'
    )

    # It should be named 'skills'
    assert GLOBAL_SKILLS_DIR.name == 'skills', (
        f"Expected directory name 'skills', got '{GLOBAL_SKILLS_DIR.name}'"
    )

    # It should contain at least one .md file (skill definition)
    md_files = list(GLOBAL_SKILLS_DIR.glob('*.md'))
    assert len(md_files) > 0, (
        f'GLOBAL_SKILLS_DIR contains no .md files: {GLOBAL_SKILLS_DIR}'
    )

    # Verify it's at repo root by checking for known skill files
    # (github.md is a core skill that should always exist)
    expected_skill = GLOBAL_SKILLS_DIR / 'github.md'
    assert expected_skill.exists(), (
        f'Expected skill file not found: {expected_skill}. '
        f'GLOBAL_SKILLS_DIR may be pointing to wrong location.'
    )


# Tests for marketplace-skills endpoint git clone failures


def _clone_url(mock_run: MagicMock) -> str:
    """Return the URL passed to the first ``git clone`` call."""
    argv = mock_run.call_args_list[0][0][0]
    return argv[argv.index('--') + 1]


class TestMarketplaceSkillsCloneFailures:
    """Tests for git clone failure scenarios in marketplace-skills endpoint."""

    @pytest.mark.asyncio
    async def test_clone_invalid_repo_path(self):
        """Test that invalid repository path returns appropriate error."""
        mock_user_context = MagicMock()
        mock_user_context.get_provider_tokens = AsyncMock(return_value=None)
        mock_user_context.get_user_id = AsyncMock(return_value='test-user')

        marketplace = MarketplaceRegistration(
            name='test-marketplace',
            source='github:owner/valid-repo',
        )

        # Mock _parse_marketplace_source to return invalid path (empty = parse failure)
        with patch(
            'openhands.app_server.user.skills_router._parse_marketplace_source',
            return_value=('github', ''),
        ):
            result = await _clone_marketplace_repo(marketplace, mock_user_context)

        assert result[0] is None
        assert 'Invalid repository path' in result[1]

    @pytest.mark.asyncio
    async def test_clone_network_timeout(self):
        """Test that network timeout returns appropriate error."""

        mock_user_context = MagicMock()
        mock_user_context.get_provider_tokens = AsyncMock(return_value=None)
        mock_user_context.get_user_id = AsyncMock(return_value='test-user')

        marketplace = MarketplaceRegistration(
            name='test-marketplace',
            source='github:owner/nonexistent-repo-12345',
        )

        # Mock subprocess.run to raise TimeoutExpired
        with patch(
            'openhands.app_server.user.skills_router.subprocess.run'
        ) as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd='git clone', timeout=120
            )
            result = await _clone_marketplace_repo(marketplace, mock_user_context)

        assert result[0] is None
        assert 'timed out' in result[1]

    @pytest.mark.asyncio
    async def test_clone_git_failure_returns_error(self):
        """Test that git clone failure returns error message."""
        mock_user_context = MagicMock()
        mock_user_context.get_provider_tokens = AsyncMock(return_value=None)
        mock_user_context.get_user_id = AsyncMock(return_value='test-user')

        marketplace = MarketplaceRegistration(
            name='test-marketplace',
            source='github:owner/nonexistent-repo-xyz',
        )

        # Mock subprocess.run to return error
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stderr = 'Repository not found'

        with patch(
            'openhands.app_server.user.skills_router.subprocess.run'
        ) as mock_run:
            mock_run.return_value = mock_result
            result = await _clone_marketplace_repo(marketplace, mock_user_context)

        assert result[0] is None
        assert 'Git clone failed' in result[1]
        assert 'not found' in result[1].lower()

    @pytest.mark.asyncio
    async def test_clone_with_ref_checkout_failure(self):
        """Test that checkout ref failure returns appropriate error."""
        mock_user_context = MagicMock()
        mock_user_context.get_provider_tokens = AsyncMock(return_value=None)
        mock_user_context.get_user_id = AsyncMock(return_value='test-user')

        marketplace = MarketplaceRegistration(
            name='test-marketplace',
            source='github:owner/valid-repo',
            ref='0123456789abcdef0123456789abcdef01234567',
        )

        # Mock subprocess.run for clone success, checkout failure
        mock_clone_result = MagicMock()
        mock_clone_result.returncode = 0

        mock_checkout_result = MagicMock()
        mock_checkout_result.returncode = 128
        mock_checkout_result.stderr = ' reference is not a tree'

        def run_side_effect(cmd, *args, **kwargs):
            if 'checkout' in cmd:
                return mock_checkout_result
            return mock_clone_result

        with patch(
            'openhands.app_server.user.skills_router.subprocess.run'
        ) as mock_run:
            with patch('openhands.app_server.user.skills_router._cleanup_clone_dir'):
                with patch('tempfile.mkdtemp', return_value=Path('/tmp/test_clone')):
                    mock_run.side_effect = run_side_effect
                    result = await _clone_marketplace_repo(
                        marketplace, mock_user_context
                    )

        assert result[0] is None
        assert 'Git checkout failed' in result[1]

    @pytest.mark.asyncio
    async def test_clone_repo_path_not_found(self):
        """Test that non-existent repo_path returns appropriate error."""
        mock_user_context = MagicMock()
        mock_user_context.get_provider_tokens = AsyncMock(return_value=None)
        mock_user_context.get_user_id = AsyncMock(return_value='test-user')

        marketplace = MarketplaceRegistration(
            name='test-marketplace',
            source='github:owner/valid-repo',
            repo_path='nonexistent/skills/dir',
        )

        mock_clone_result = MagicMock()
        mock_clone_result.returncode = 0

        def run_side_effect(cmd, *args, **kwargs):
            return mock_clone_result

        with patch(
            'openhands.app_server.user.skills_router.subprocess.run'
        ) as mock_run:
            with patch(
                'openhands.app_server.user.skills_router.Path.exists'
            ) as mock_exists:
                with patch(
                    'openhands.app_server.user.skills_router._cleanup_clone_dir'
                ):
                    with patch(
                        'tempfile.mkdtemp', return_value=Path('/tmp/test_clone')
                    ):
                        mock_run.side_effect = run_side_effect
                        # First call is clone, exists() returns False for repo_path
                        mock_exists.return_value = False
                        result = await _clone_marketplace_repo(
                            marketplace, mock_user_context
                        )

        assert result[0] is None
        assert 'Repo path not found' in result[1]

    @pytest.mark.asyncio
    async def test_clone_bbdc_personal_url_uses_pinned_authenticated_url(self):
        """A BBDC personal-repo URL clones via the host-matched, pinned token URL."""
        handler = AsyncMock()
        dc_url = 'https://user:pat@bitbucket.dc.example.com/scm/~jane/marketplace.git'
        handler.get_authenticated_git_url.return_value = dc_url

        mock_user_context = MagicMock()
        mock_user_context.get_provider_tokens = AsyncMock(
            return_value={
                ProviderType.BITBUCKET_DATA_CENTER: ProviderToken(
                    host='bitbucket.dc.example.com'
                )
            }
        )
        mock_user_context.get_user_id = AsyncMock(return_value='test-user')
        mock_user_context.get_provider_handler = AsyncMock(return_value=handler)

        marketplace = MarketplaceRegistration(
            name='personal',
            source='https://bitbucket.dc.example.com/scm/~jane/marketplace.git',
        )

        mock_clone = MagicMock(returncode=0)
        with patch(
            'openhands.app_server.user.skills_router.subprocess.run',
            return_value=mock_clone,
        ) as mock_run:
            with patch('tempfile.mkdtemp', return_value=Path('/tmp/test_clone')):
                await _clone_marketplace_repo(marketplace, mock_user_context)

        handler.get_authenticated_git_url.assert_awaited_once_with(
            '~jane/marketplace',
            specified_provider=ProviderType.BITBUCKET_DATA_CENTER,
        )
        assert _clone_url(mock_run) == dc_url

    @pytest.mark.asyncio
    async def test_clone_azure_devops_encoded_url_encodes_project_once(self):
        """An Azure URL with an encoded project name clones the same repository."""
        service = AsyncMock()
        service.get_repository_details_from_repo_name.return_value = Repository(
            id='1',
            full_name='acme/Web Apps/skills',
            git_provider=ProviderType.AZURE_DEVOPS,
            is_public=False,
        )
        handler = ProviderHandler(
            provider_tokens=MappingProxyType(
                {ProviderType.AZURE_DEVOPS: ProviderToken(token=SecretStr('pat'))}
            )
        )

        mock_user_context = MagicMock()
        mock_user_context.get_provider_tokens = AsyncMock(
            return_value=handler.provider_tokens
        )
        mock_user_context.get_provider_handler = AsyncMock(return_value=handler)
        mock_user_context.get_latest_token = AsyncMock(return_value='pat')

        marketplace = MarketplaceRegistration(
            name='azure',
            source='https://acme@dev.azure.com/acme/Web%20Apps/_git/skills',
        )

        mock_clone = MagicMock(returncode=0)
        with (
            patch.object(handler, 'get_service', return_value=service),
            patch(
                'openhands.app_server.user.skills_router.subprocess.run',
                return_value=mock_clone,
            ) as mock_run,
            patch('tempfile.mkdtemp', return_value=Path('/tmp/test_clone')),
        ):
            await _clone_marketplace_repo(marketplace, mock_user_context)

        service.get_repository_details_from_repo_name.assert_awaited_once_with(
            'acme/Web Apps/skills'
        )
        assert _clone_url(mock_run) == (
            'https://acme:pat@dev.azure.com/acme/Web%20Apps/_git/skills'
        )
        # PATs are already in the URL; no extra header is configured.
        assert mock_run.call_args.kwargs['env'] is None

    @pytest.mark.asyncio
    async def test_clone_azure_devops_entra_token_sent_as_header_not_url(self):
        """An Entra token reaches git as a Bearer header, never in argv or URL."""
        token = 'header.payload.signature'
        service = AsyncMock()
        service.get_repository_details_from_repo_name.return_value = Repository(
            id='1',
            full_name='acme/Web Apps/skills',
            git_provider=ProviderType.AZURE_DEVOPS,
            is_public=False,
        )
        handler = ProviderHandler(
            provider_tokens=MappingProxyType(
                {ProviderType.AZURE_DEVOPS: ProviderToken(token=SecretStr(token))}
            )
        )

        mock_user_context = MagicMock()
        mock_user_context.get_provider_tokens = AsyncMock(
            return_value=handler.provider_tokens
        )
        mock_user_context.get_provider_handler = AsyncMock(return_value=handler)
        mock_user_context.get_latest_token = AsyncMock(return_value=token)

        marketplace = MarketplaceRegistration(
            name='azure',
            source='https://acme@dev.azure.com/acme/Web%20Apps/_git/skills',
        )

        mock_clone = MagicMock(returncode=0)
        with (
            patch.object(handler, 'get_service', return_value=service),
            patch(
                'openhands.app_server.user.skills_router.subprocess.run',
                return_value=mock_clone,
            ) as mock_run,
            patch('tempfile.mkdtemp', return_value=Path('/tmp/test_clone')),
            patch.dict(os.environ, {'GIT_CONFIG_COUNT': '0'}),
        ):
            await _clone_marketplace_repo(marketplace, mock_user_context)

        clone_argv = mock_run.call_args[0][0]
        assert _clone_url(mock_run) == (
            'https://dev.azure.com/acme/Web%20Apps/_git/skills'
        )
        assert not any(token in arg for arg in clone_argv)
        env = mock_run.call_args.kwargs['env']
        assert env['GIT_CONFIG_COUNT'] == '1'
        assert env['GIT_CONFIG_KEY_0'] == 'http.https://dev.azure.com/.extraheader'
        assert env['GIT_CONFIG_VALUE_0'] == f'Authorization: Bearer {token}'

    def test_bearer_auth_git_env_scopes_header_to_clone_origin(self):
        """The header is scoped to the clone URL's origin, including its port."""
        with patch.dict(os.environ, {'GIT_CONFIG_COUNT': '0'}):
            env = _bearer_auth_git_env('http://127.0.0.1:8080/o/p/_git/r', 'tok')

        assert env is not None
        assert env['GIT_CONFIG_KEY_0'] == 'http.http://127.0.0.1:8080/.extraheader'
        assert _bearer_auth_git_env('https://dev.azure.com/o/p/_git/r', None) is None

    def test_bearer_auth_git_env_appends_to_inherited_git_config(self):
        """Inherited GIT_CONFIG_* entries are kept; the header is appended."""
        inherited = {
            'GIT_CONFIG_COUNT': '1',
            'GIT_CONFIG_KEY_0': 'http.sslCAInfo',
            'GIT_CONFIG_VALUE_0': '/etc/ca.pem',
        }
        with patch.dict(os.environ, inherited):
            env = _bearer_auth_git_env('https://dev.azure.com/o/p/_git/r', 'tok')

        assert env is not None
        assert env['GIT_CONFIG_COUNT'] == '2'
        assert env['GIT_CONFIG_KEY_0'] == 'http.sslCAInfo'
        assert env['GIT_CONFIG_KEY_1'] == 'http.https://dev.azure.com/.extraheader'
        assert env['GIT_CONFIG_VALUE_1'] == 'Authorization: Bearer tok'

    @pytest.mark.asyncio
    async def test_clone_refuses_unmatched_custom_host_url(self):
        """A full-URL source on a host matching no provider is refused (no SSRF)."""
        mock_user_context = MagicMock()
        mock_user_context.get_provider_tokens = AsyncMock(
            return_value={
                ProviderType.BITBUCKET_DATA_CENTER: ProviderToken(
                    host='bitbucket.dc.example.com'
                )
            }
        )
        mock_user_context.get_user_id = AsyncMock(return_value='test-user')
        mock_user_context.get_provider_handler = AsyncMock()

        marketplace = MarketplaceRegistration(
            name='evil',
            source='https://attacker.example.com/scm/proj/repo.git',
        )

        with patch(
            'openhands.app_server.user.skills_router.subprocess.run'
        ) as mock_run:
            result = await _clone_marketplace_repo(marketplace, mock_user_context)

        assert result[0] is None
        assert 'Unsupported marketplace host' in result[1]
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_clone_public_github_url_works_with_non_github_provider(self):
        """A public github.com URL is a public clone even for a Bitbucket-only user."""
        mock_user_context = MagicMock()
        mock_user_context.get_provider_tokens = AsyncMock(
            return_value={
                ProviderType.BITBUCKET_DATA_CENTER: ProviderToken(
                    host='bitbucket.dc.example.com'
                )
            }
        )
        mock_user_context.get_user_id = AsyncMock(return_value='test-user')
        mock_user_context.get_provider_handler = AsyncMock()

        marketplace = MarketplaceRegistration(
            name='public-gh',
            source='https://github.com/OpenHands/skills.git',
        )

        mock_clone = MagicMock(returncode=0)
        with patch(
            'openhands.app_server.user.skills_router.subprocess.run',
            return_value=mock_clone,
        ) as mock_run:
            with patch('tempfile.mkdtemp', return_value=Path('/tmp/test_clone')):
                await _clone_marketplace_repo(marketplace, mock_user_context)

        # Not refused; cloned from the public github.com domain (no credentials).
        assert _clone_url(mock_run) == 'https://github.com/OpenHands/skills.git'
        mock_user_context.get_provider_handler.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'source', ['github:dotnet/skills', 'https://github.com/dotnet/skills.git']
    )
    async def test_github_source_skips_azure_devops_token(self, source):
        """An Azure-only user clones public GitHub without an Azure lookup."""
        mock_user_context = MagicMock()
        mock_user_context.get_provider_tokens = AsyncMock(
            return_value={
                ProviderType.AZURE_DEVOPS: ProviderToken(token=SecretStr('pat'))
            }
        )
        mock_user_context.get_user_id = AsyncMock(return_value='test-user')
        mock_user_context.get_provider_handler = AsyncMock()
        marketplace = MarketplaceRegistration(name='dotnet', source=source)

        with (
            patch.object(ProviderHandler, 'get_authenticated_git_url') as mock_resolve,
            patch(
                'openhands.app_server.user.skills_router.subprocess.run',
                return_value=MagicMock(returncode=0),
            ) as mock_run,
            patch('tempfile.mkdtemp', return_value=Path('/tmp/test_clone')),
        ):
            result = await _clone_marketplace_repo(marketplace, mock_user_context)

        assert result == (Path('/tmp/test_clone'), '')
        mock_resolve.assert_not_called()
        mock_user_context.get_provider_handler.assert_not_awaited()
        assert _clone_url(mock_run) == 'https://github.com/dotnet/skills.git'
        assert mock_run.call_args_list[0].kwargs['env'] is None

    @pytest.mark.asyncio
    async def test_github_source_resolves_only_against_github(self):
        """With a GitHub token, a github: source is resolved pinned to GitHub."""
        github_url = 'https://x-access-token:gh@github.com/acme/private-skills.git'
        mock_user_context = MagicMock()
        mock_user_context.get_provider_tokens = AsyncMock(
            return_value={
                ProviderType.AZURE_DEVOPS: ProviderToken(token=SecretStr('pat')),
                ProviderType.GITHUB: ProviderToken(token=SecretStr('gh')),
            }
        )
        mock_user_context.get_user_id = AsyncMock(return_value='test-user')
        marketplace = MarketplaceRegistration(
            name='private', source='github:acme/private-skills'
        )

        with (
            patch.object(
                ProviderHandler, 'get_authenticated_git_url', return_value=github_url
            ) as mock_resolve,
            patch(
                'openhands.app_server.user.skills_router.subprocess.run',
                return_value=MagicMock(returncode=0),
            ) as mock_run,
            patch('tempfile.mkdtemp', return_value=Path('/tmp/test_clone')),
        ):
            await _clone_marketplace_repo(marketplace, mock_user_context)

        mock_resolve.assert_awaited_once_with(
            'acme/private-skills', specified_provider=ProviderType.GITHUB
        )
        assert _clone_url(mock_run) == github_url

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'ref, clone_flags, checkout',
        [
            (None, ['--depth', '1'], False),
            ('v1.2', ['--depth', '1', '--branch', 'v1.2'], False),
            ('0123456789abcdef0123456789abcdef01234567', [], True),
        ],
    )
    async def test_clone_is_shallow_unless_ref_is_commit_sha(
        self, ref, clone_flags, checkout
    ):
        """Branches and tags clone shallow; only a commit SHA needs history."""
        mock_user_context = MagicMock()
        mock_user_context.get_provider_tokens = AsyncMock(return_value=None)
        marketplace = MarketplaceRegistration(
            name='dotnet', source='github:dotnet/skills', ref=ref
        )

        with (
            patch(
                'openhands.app_server.user.skills_router.subprocess.run',
                return_value=MagicMock(returncode=0),
            ) as mock_run,
            patch('tempfile.mkdtemp', return_value=Path('/tmp/test_clone')),
        ):
            await _clone_marketplace_repo(marketplace, mock_user_context)

        clone_argv = mock_run.call_args_list[0][0][0]
        assert clone_argv == [
            'git',
            'clone',
            *clone_flags,
            '--',
            'https://github.com/dotnet/skills.git',
            '/tmp/test_clone',
        ]
        checkout_calls = [c for c in mock_run.call_args_list if 'checkout' in c[0][0]]
        assert len(checkout_calls) == (1 if checkout else 0)


# Tests for marketplace-skills endpoint built-in skill name conflicts


def _write_skill_dir(skill_dir: Path, name: str, description: str = '') -> None:
    """Write a skill directory whose SKILL.md declares the given name."""
    skill_dir.mkdir(parents=True)
    frontmatter = f'name: {name}\n'
    if description:
        frontmatter += f'description: {description}\n'
    (skill_dir / 'SKILL.md').write_text(f'---\n{frontmatter}---\n{name} content')


def _write_marketplace_manifest(repo_dir: Path, layout: str, source: str) -> None:
    """Write a marketplace manifest listing one entry as a plugin or a skill."""
    entry = {'name': 'custom', 'source': source}
    manifest = {
        'name': 'acme',
        'owner': {'name': 'Acme'},
        'plugins': [entry] if layout == 'plugin' else [],
        'skills': [entry] if layout == 'skill' else [],
    }
    manifest_dir = repo_dir / '.plugin'
    manifest_dir.mkdir(parents=True)
    (manifest_dir / 'marketplace.json').write_text(json.dumps(manifest))


async def _preview_marketplace(repo_dir: Path, global_dir: Path):
    """Preview a marketplace whose clone is repo_dir against built-in skills."""
    marketplace = MarketplaceRegistration(name='acme', source='github:acme/skills')
    with (
        patch(
            'openhands.app_server.user.skills_router._clone_marketplace_repo',
            AsyncMock(return_value=(repo_dir, '')),
        ),
        patch('openhands.app_server.user.skills_router.GLOBAL_SKILLS_DIR', global_dir),
    ):
        return await get_marketplace_skills([marketplace], user_context=MagicMock())


class TestMarketplaceSkillsNameConflicts:
    """Tests for built-in skill name conflicts in marketplace-skills endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize('layout', ['plugin', 'skill', 'no-manifest'])
    async def test_reports_skill_sharing_builtin_name(self, tmp_path, layout):
        """A marketplace skill named like a built-in skill is reported."""
        # Arrange
        global_dir = tmp_path / 'global'
        _write_skill_file(global_dir, 'github')
        repo_dir = tmp_path / 'repo'
        _write_skill_dir(repo_dir / 'skills' / 'custom', 'github')
        if layout != 'no-manifest':
            _write_marketplace_manifest(repo_dir, layout, './skills/custom')

        # Act
        result = await _preview_marketplace(repo_dir, global_dir)

        # Assert
        assert [conflict.model_dump() for conflict in result.conflicts] == [
            {
                'name': 'github',
                'marketplace': 'acme',
                'source': 'github:acme/skills',
                'conflicts_with': 'global',
            }
        ]
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_reports_no_conflict_for_unique_skill_name(self, tmp_path):
        """A marketplace skill with a unique name is not reported."""
        # Arrange
        global_dir = tmp_path / 'global'
        _write_skill_file(global_dir, 'github')
        repo_dir = tmp_path / 'repo'
        _write_skill_dir(repo_dir / 'skills' / 'custom', 'acme-github')
        _write_marketplace_manifest(repo_dir, 'plugin', './skills/custom')

        # Act
        result = await _preview_marketplace(repo_dir, global_dir)

        # Assert
        assert result.conflicts == []

    @pytest.mark.asyncio
    async def test_ignores_manifest_entry_outside_marketplace_repo(self, tmp_path):
        """A manifest entry pointing outside the cloned repo is not inspected."""
        # Arrange
        global_dir = tmp_path / 'global'
        _write_skill_file(global_dir, 'github')
        _write_skill_dir(tmp_path / 'outside', 'github')
        repo_dir = tmp_path / 'repo'
        _write_marketplace_manifest(repo_dir, 'plugin', '../outside')

        # Act
        result = await _preview_marketplace(repo_dir, global_dir)

        # Assert
        assert result.conflicts == []


# Tests for marketplace-skills preview of repositories without a manifest


class TestMarketplaceSkillsPlainRepository:
    """The preview lists the skills a conversation loads from a plain repo."""

    @pytest.mark.asyncio
    async def test_lists_skill_folders_in_skills_dirs(self, tmp_path):
        """Skill folders in skills/ and .skills/ are listed; loose markdown is not."""
        # Arrange
        repo_dir = tmp_path / 'repo'
        _write_skill_dir(repo_dir / 'skills' / 'greet', 'greet')
        _write_skill_dir(repo_dir / '.skills' / 'commit', 'commit')
        _write_skill_file(repo_dir / 'skills', 'notes')
        _write_skill_dir(repo_dir / 'stray', 'stray')

        # Act
        result = await _preview_marketplace(repo_dir, tmp_path / 'global')

        # Assert
        assert [skill.name for skill in result.skills] == ['greet', 'commit']
        assert result.marketplace_skills == {'acme': ['greet', 'commit']}
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_lists_skill_folders_when_path_is_the_skills_folder(self, tmp_path):
        """A repository path pointing at the skills folder lists its skills."""
        # Arrange: the clone path is already the repository path (skills/).
        skills_dir = tmp_path / 'repo' / 'skills'
        _write_skill_dir(skills_dir / 'greet', 'greet')

        # Act
        result = await _preview_marketplace(skills_dir, tmp_path / 'global')

        # Assert
        assert [skill.name for skill in result.skills] == ['greet']
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_skips_skills_conversations_cannot_load(self, tmp_path):
        """A skill the SDK can't load is not listed, as conversations skip it."""
        # Arrange
        skills_dir = tmp_path / 'repo' / 'skills'
        _write_skill_dir(skills_dir / 'greet', 'greet')
        bad_frontmatter = skills_dir / 'deploy'
        bad_frontmatter.mkdir()
        (bad_frontmatter / 'SKILL.md').write_text(
            '---\nname: deploy\ndescription: Use when: deploying\n---\nDeploy.'
        )
        _write_skill_dir(skills_dir / 'tools', 'tools')
        (skills_dir / 'tools' / '.mcp.json').write_text('{not json')

        # Act
        result = await _preview_marketplace(tmp_path / 'repo', tmp_path / 'global')

        # Assert
        assert [skill.name for skill in result.skills] == ['greet']
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_reports_repository_without_manifest_or_skills(self, tmp_path):
        """A repository a conversation would load nothing from is an error."""
        # Arrange
        repo_dir = tmp_path / 'repo'
        _write_skill_file(repo_dir, 'readme-like')
        _write_skill_file(repo_dir / 'docs', 'guide')

        # Act
        result = await _preview_marketplace(repo_dir, tmp_path / 'global')

        # Assert
        assert result.skills == []
        assert result.errors == [
            'acme: no marketplace manifest or skill folders found (expected '
            '.plugin/marketplace.json or skills/<name>/SKILL.md)'
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'plugin_manifest, layout',
        [
            (None, 'skills'),
            ('.claude-plugin/plugin.json', 'plugin'),
            ('.plugin/plugin.json', 'plugin'),
        ],
    )
    async def test_reports_how_a_repository_without_manifest_is_read(
        self, tmp_path, plugin_manifest, layout
    ):
        """A plugin without a marketplace manifest is told apart from a skills
        folder, since conversations load only its skills."""
        # Arrange
        repo_dir = tmp_path / 'repo'
        _write_skill_dir(repo_dir / 'skills' / 'greet', 'greet')
        if plugin_manifest:
            manifest = repo_dir / plugin_manifest
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({'name': 'acme'}))

        # Act
        result = await _preview_marketplace(repo_dir, tmp_path / 'global')

        # Assert
        assert [skill.name for skill in result.skills] == ['greet']
        assert result.marketplace_layouts == {'acme': layout}

    @pytest.mark.asyncio
    async def test_repository_with_manifest_is_read_as_marketplace(self, tmp_path):
        """A marketplace manifest wins over the plugin and skill folders."""
        # Arrange
        repo_dir = tmp_path / 'repo'
        _write_skill_dir(repo_dir / 'skills' / 'custom', 'custom')
        _write_marketplace_manifest(repo_dir, 'skill', './skills/custom')
        (repo_dir / '.plugin' / 'plugin.json').write_text(json.dumps({'name': 'x'}))

        # Act
        result = await _preview_marketplace(repo_dir, tmp_path / 'global')

        # Assert
        assert result.marketplace_layouts == {'acme': 'marketplace'}

    @pytest.mark.asyncio
    async def test_invalid_manifest_is_not_read_as_plain_repository(self, tmp_path):
        """A broken manifest is reported, not replaced by the skills folder."""
        # Arrange
        repo_dir = tmp_path / 'repo'
        _write_skill_dir(repo_dir / 'skills' / 'greet', 'greet')
        (repo_dir / '.plugin').mkdir()
        (repo_dir / '.plugin' / 'marketplace.json').write_text('{not json')

        # Act
        result = await _preview_marketplace(repo_dir, tmp_path / 'global')

        # Assert
        assert result.skills == []
        assert result.errors == ['acme: invalid marketplace manifest']


# Tests for the marketplace-check endpoint (checked before saving)

_ROUTER = 'openhands.app_server.user.skills_router'


class TestSkillDescriptions:
    """Skill descriptions are returned so the settings page can show them."""

    @pytest.mark.asyncio
    async def test_search_returns_frontmatter_description(self, test_client, tmp_path):
        # Arrange
        global_dir = tmp_path / 'global'
        _write_skill_file(global_dir, 'described', description='Does a thing')
        _write_skill_file(global_dir, 'undescribed')

        # Act
        with (
            patch(
                'openhands.app_server.user.skills_router.GLOBAL_SKILLS_DIR', global_dir
            ),
            patch(
                'openhands.app_server.user.skills_router.USER_SKILLS_DIR',
                tmp_path / 'nonexistent',
            ),
        ):
            response = test_client.get('/api/v1/skills/search')

        # Assert
        assert response.status_code == 200
        descriptions = {s['name']: s['description'] for s in response.json()['items']}
        assert descriptions == {'described': 'Does a thing', 'undescribed': None}

    @pytest.mark.asyncio
    async def test_search_keeps_skill_with_non_string_description(
        self, test_client, tmp_path
    ):
        # Arrange: YAML parses an unquoted number as an int, not a string.
        global_dir = tmp_path / 'global'
        _write_skill_file(global_dir, 'numeric', description='42')

        # Act
        with (
            patch(
                'openhands.app_server.user.skills_router.GLOBAL_SKILLS_DIR', global_dir
            ),
            patch(
                'openhands.app_server.user.skills_router.USER_SKILLS_DIR',
                tmp_path / 'nonexistent',
            ),
        ):
            response = test_client.get('/api/v1/skills/search')

        # Assert
        assert [(s['name'], s['description']) for s in response.json()['items']] == [
            ('numeric', '42')
        ]

    @pytest.mark.asyncio
    async def test_plain_repository_skill_description(self, tmp_path):
        # Arrange
        repo_dir = tmp_path / 'repo'
        _write_skill_dir(repo_dir / 'skills' / 'greet', 'greet', 'Greets people')
        _write_skill_dir(repo_dir / 'skills' / 'wave', 'wave')

        # Act
        result = await _preview_marketplace(repo_dir, tmp_path / 'global')

        # Assert
        assert [(s.name, s.description) for s in result.skills] == [
            ('greet', 'Greets people'),
            ('wave', None),
        ]

    @pytest.mark.asyncio
    async def test_manifest_skill_and_plugin_descriptions(self, tmp_path):
        # Arrange
        repo_dir = tmp_path / 'repo'
        _write_skill_dir(repo_dir / 'skills' / 'custom', 'custom')
        _write_skill_dir(repo_dir / 'plugins' / 'tools' / 'skills' / 'x', 'x')
        manifest = {
            'name': 'acme',
            'owner': {'name': 'Acme'},
            'plugins': [
                {
                    'name': 'tools',
                    'source': './plugins/tools',
                    'description': 'A plugin',
                }
            ],
            'skills': [
                {
                    'name': 'custom',
                    'source': './skills/custom',
                    'description': 'A skill',
                }
            ],
        }
        (repo_dir / '.plugin').mkdir()
        (repo_dir / '.plugin' / 'marketplace.json').write_text(json.dumps(manifest))

        # Act
        result = await _preview_marketplace(repo_dir, tmp_path / 'global')

        # Assert
        assert [(s.name, s.description) for s in result.skills] == [
            ('custom', 'A skill')
        ]
        assert [(p.name, p.description) for p in result.plugins] == [
            ('tools', 'A plugin')
        ]


def _preview(
    plugins: int = 0,
    errors: list[str] | None = None,
    layout: str | None = None,
) -> MarketplaceSkillsPreviewResponse:
    return MarketplaceSkillsPreviewResponse(
        skills=[],
        plugins=[
            MarketplacePluginPreview(
                name=f'plugin-{i}', source='github:acme/skills', marketplace='acme'
            )
            for i in range(plugins)
        ],
        marketplace_skills={},
        errors=errors or [],
        marketplace_layouts={'acme': layout} if layout else {},
    )


async def _check(
    source: str,
    *,
    resolved: str | None = None,
    preview: MarketplaceSkillsPreviewResponse | Exception | None = None,
    provider_match: tuple[ProviderType, str] | None = None,
    ref: str | None = None,
):
    """Run check_marketplace with conversation resolution and preview mocked."""
    marketplace = MarketplaceRegistration(name='acme', source=source, ref=ref)

    async def authenticate(registrations, _user_context):
        return [
            reg.model_copy(update={'source': resolved}) if resolved is not None else reg
            for reg in registrations
        ]

    preview_mock = AsyncMock(
        side_effect=preview if isinstance(preview, Exception) else None,
        return_value=preview if preview is not None else _preview(plugins=1),
    )
    with (
        patch(f'{_ROUTER}.authenticate_marketplace_sources', side_effect=authenticate),
        patch(
            f'{_ROUTER}._match_url_source_to_provider',
            AsyncMock(return_value=provider_match),
        ),
        patch(f'{_ROUTER}.get_marketplace_skills', preview_mock),
    ):
        result = await check_marketplace(marketplace, user_context=MagicMock())
    return result, preview_mock


class TestMarketplaceCheck:
    """The check reports whether a marketplace will load in conversations."""

    @pytest.mark.asyncio
    async def test_repository_that_loads_is_ok(self):
        result, preview = await _check('github:acme/skills', preview=_preview(2))

        assert (result.status, result.reason, result.plugin_count) == ('ok', None, 2)
        assert preview.await_args.args[0][0].source == 'github:acme/skills'

    @pytest.mark.asyncio
    async def test_source_without_scheme_is_checked_as_https(self):
        result, preview = await _check('github.com/acme/skills')

        assert result.status == 'ok'
        assert preview.await_args.args[0][0].source == (
            'https://github.com/acme/skills'
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'error, status, reason',
        [
            (
                "acme: Git clone failed: fatal: repository 'x' not found",
                'error',
                'not_found',
            ),
            ('acme: Unsupported marketplace host: x', 'error', 'not_found'),
            (
                "acme: Git checkout failed: error: pathspec 'v9' did not match",
                'error',
                'ref_not_found',
            ),
            (
                'acme: Git clone failed: fatal: Remote branch v9 not found in '
                'upstream origin',
                'error',
                'ref_not_found',
            ),
            ('acme: Repo path not found: plugins', 'error', 'path_not_found'),
            (
                'acme: no marketplace manifest or skill folders found (expected '
                '.plugin/marketplace.json or skills/<name>/SKILL.md)',
                'error',
                'empty',
            ),
            ('acme: Git clone timed out', 'unchecked', 'failed'),
            ('acme: Clone failed: unexpected', 'unchecked', 'failed'),
        ],
    )
    async def test_preview_errors_are_classified(self, error, status, reason):
        result, _ = await _check('github:acme/skills', preview=_preview(errors=[error]))

        assert (result.status, result.reason) == (status, reason)

    @pytest.mark.asyncio
    async def test_repository_without_plugins_or_skills_is_an_error(self):
        result, _ = await _check('github:acme/skills', preview=_preview(0))

        assert (result.status, result.reason) == ('error', 'empty')

    @pytest.mark.asyncio
    @pytest.mark.parametrize('layout', ['marketplace', 'skills', 'plugin'])
    async def test_reports_how_a_repository_that_loads_is_read(self, layout):
        result, _ = await _check(
            'github:acme/skills', preview=_preview(1, layout=layout)
        )

        assert (result.status, result.layout) == ('ok', layout)

    @pytest.mark.asyncio
    async def test_plugin_without_marketplace_manifest_is_reported(self, tmp_path):
        """End to end through the preview: the plugin's skills pass the check,
        and the layout says only they will load."""
        # Arrange
        repo_dir = tmp_path / 'repo'
        _write_skill_dir(repo_dir / 'skills' / 'greet', 'greet')
        (repo_dir / '.claude-plugin').mkdir()
        (repo_dir / '.claude-plugin' / 'plugin.json').write_text('{"name": "acme"}')
        marketplace = MarketplaceRegistration(name='acme', source='github:acme/skills')

        # Act
        with (
            patch(
                f'{_ROUTER}.authenticate_marketplace_sources',
                AsyncMock(side_effect=lambda regs, _ctx: regs),
            ),
            patch(
                f'{_ROUTER}._clone_marketplace_repo',
                AsyncMock(return_value=(repo_dir, '')),
            ),
            patch(f'{_ROUTER}.GLOBAL_SKILLS_DIR', tmp_path / 'global'),
        ):
            result = await check_marketplace(marketplace, user_context=MagicMock())

        # Assert
        assert (result.status, result.skill_count, result.layout) == ('ok', 1, 'plugin')

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'source', ['OpenHands/extensions', 'marketplaces/team/internal', 'skills']
    )
    async def test_source_read_as_a_sandbox_folder_is_unchecked(self, source):
        """A source no connected provider resolves is read as a folder, so it
        loads nothing from a repository."""
        result, preview = await _check(source)

        assert (result.status, result.reason) == ('unchecked', 'sandbox_folder')
        preview.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_owner_repo_resolved_by_a_provider_is_checked(self):
        result, preview = await _check(
            'acme/skills',
            resolved='https://x-access-token:secret-token@github.com/acme/skills.git',
        )

        assert result.status == 'ok'
        preview.assert_awaited_once()
        assert 'secret-token' not in result.model_dump_json()

    @pytest.mark.asyncio
    async def test_host_that_is_not_a_connected_provider_is_unchecked(self):
        result, preview = await _check('https://gitea.example.org/org/skills')

        assert (result.status, result.reason) == ('unchecked', 'unknown_host')
        preview.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_connected_self_hosted_provider_is_checked(self):
        result, preview = await _check(
            'gitlab.example.com/group/skills',
            provider_match=(ProviderType.GITLAB, 'group/skills'),
        )

        assert result.status == 'ok'
        preview.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_source_the_agent_server_cannot_parse_is_an_error(self):
        result, preview = await _check('ssh://git@github.com/acme/skills.git')

        assert (result.status, result.reason) == ('error', 'unsupported')
        preview.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_preview_failure_is_unchecked(self):
        result, _ = await _check(
            'github:acme/skills', preview=HTTPException(status_code=500)
        )

        assert (result.status, result.reason) == ('unchecked', 'failed')


def test_marketplace_check_endpoint_validates_and_normalizes(test_client):
    with (
        patch(
            f'{_ROUTER}.authenticate_marketplace_sources',
            AsyncMock(side_effect=lambda regs, _ctx: regs),
        ),
        patch(
            f'{_ROUTER}.get_marketplace_skills',
            AsyncMock(return_value=_preview(1)),
        ) as preview,
    ):
        ok = test_client.post(
            '/api/v1/skills/marketplace-check',
            json={'name': 'acme', 'source': 'github.com/acme/skills'},
        )
        invalid = test_client.post(
            '/api/v1/skills/marketplace-check',
            json={'name': 'acme', 'source': '/etc/skills'},
        )

    assert ok.status_code == 200
    assert ok.json()['status'] == 'ok'
    assert preview.await_args.args[0][0].source == 'https://github.com/acme/skills'
    assert invalid.status_code == 422
