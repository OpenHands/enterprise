"""Unit tests for git and security functionality in AppConversationServiceBase.

This module tests the git-related functionality, specifically the clone_or_init_git_repo method
and the recent bug fixes for git checkout operations.
"""

import inspect
import os
import shutil
import stat
import subprocess
from pathlib import Path
from types import MappingProxyType, MethodType
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.app_server.app_conversation.app_conversation_models import AgentType
from openhands.app_server.app_conversation.app_conversation_service_base import (
    AppConversationServiceBase,
    _azure_devops_credential_helper_script,
)
from openhands.app_server.integrations.provider import ProviderHandler, ProviderToken
from openhands.app_server.integrations.service_types import (
    ProviderType,
    Repository,
)
from openhands.app_server.sandbox.sandbox_models import SandboxInfo
from openhands.app_server.settings.settings_models import MarketplaceRegistration
from openhands.app_server.user.auth_user_context import AuthUserContext
from openhands.app_server.user.user_context import UserContext
from openhands.sdk.skills import Skill


class MockUserInfo:
    """Mock class for UserInfo to simulate user settings."""

    def __init__(
        self,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
        git_full_clone: bool = False,
    ):
        self.git_user_name = git_user_name
        self.git_user_email = git_user_email
        self.git_full_clone = git_full_clone


class MockCommandResult:
    """Mock class for command execution result."""

    def __init__(
        self,
        exit_code: int = 0,
        stderr: str = '',
        stdout: str = '',
    ):
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout


class MockWorkspace:
    """Mock class for AsyncRemoteWorkspace."""

    def __init__(self, working_dir: str = '/workspace'):
        self.working_dir = working_dir
        self.execute_command = AsyncMock(return_value=MockCommandResult())


class MockAppConversationServiceBase:
    """Mock class to test git functionality without complex dependencies."""

    def __init__(self):
        self.logger = MagicMock()

    async def clone_or_init_git_repo(
        self,
        workspace_path: str,
        repo_url: str,
        branch: str = 'main',
        timeout: int = 300,
    ) -> bool:
        """Clone or initialize a git repository.

        This is a simplified version of the actual method for testing purposes.
        """
        try:
            # Try to clone the repository
            clone_result = subprocess.run(
                ['git', 'clone', '--branch', branch, repo_url, workspace_path],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if clone_result.returncode == 0:
                self.logger.info(
                    f'Successfully cloned repository {repo_url} to {workspace_path}'
                )
                return True

            # If clone fails, try to checkout the branch
            checkout_result = subprocess.run(
                ['git', 'checkout', branch],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if checkout_result.returncode == 0:
                self.logger.info(f'Successfully checked out branch {branch}')
                return True
            else:
                self.logger.error(
                    f'Failed to checkout branch {branch}: {checkout_result.stderr}'
                )
                return False

        except subprocess.TimeoutExpired:
            self.logger.error(f'Git operation timed out after {timeout} seconds')
            return False
        except Exception as e:
            self.logger.error(f'Git operation failed: {str(e)}')
            return False


@pytest.fixture
def service():
    """Create a mock service instance for testing."""
    return MockAppConversationServiceBase()


@pytest.mark.asyncio
async def test_clone_or_init_git_repo_successful_clone(service):
    """Test successful git clone operation."""
    with patch('subprocess.run') as mock_run:
        # Mock successful clone
        mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='Cloning...')

        result = await service.clone_or_init_git_repo(
            workspace_path='/tmp/test_repo',
            repo_url='https://github.com/test/repo.git',
            branch='main',
            timeout=300,
        )

        assert result is True
        mock_run.assert_called_once_with(
            [
                'git',
                'clone',
                '--branch',
                'main',
                'https://github.com/test/repo.git',
                '/tmp/test_repo',
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        service.logger.info.assert_called_with(
            'Successfully cloned repository https://github.com/test/repo.git to /tmp/test_repo'
        )


@pytest.mark.asyncio
async def test_clone_or_init_git_repo_clone_fails_checkout_succeeds(service):
    """Test git clone fails but checkout succeeds."""
    with patch('subprocess.run') as mock_run:
        # Mock clone failure, then checkout success
        mock_run.side_effect = [
            MagicMock(returncode=1, stderr='Clone failed', stdout=''),  # Clone fails
            MagicMock(
                returncode=0, stderr='', stdout='Switched to branch'
            ),  # Checkout succeeds
        ]

        result = await service.clone_or_init_git_repo(
            workspace_path='/tmp/test_repo',
            repo_url='https://github.com/test/repo.git',
            branch='feature-branch',
            timeout=300,
        )

        assert result is True
        assert mock_run.call_count == 2

        # Check clone call
        mock_run.assert_any_call(
            [
                'git',
                'clone',
                '--branch',
                'feature-branch',
                'https://github.com/test/repo.git',
                '/tmp/test_repo',
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )

        # Check checkout call
        mock_run.assert_any_call(
            ['git', 'checkout', 'feature-branch'],
            cwd='/tmp/test_repo',
            capture_output=True,
            text=True,
            timeout=300,
        )

        service.logger.info.assert_called_with(
            'Successfully checked out branch feature-branch'
        )


@pytest.mark.asyncio
async def test_clone_or_init_git_repo_both_operations_fail(service):
    """Test both git clone and checkout operations fail."""
    with patch('subprocess.run') as mock_run:
        # Mock both operations failing
        mock_run.side_effect = [
            MagicMock(returncode=1, stderr='Clone failed', stdout=''),  # Clone fails
            MagicMock(
                returncode=1, stderr='Checkout failed', stdout=''
            ),  # Checkout fails
        ]

        result = await service.clone_or_init_git_repo(
            workspace_path='/tmp/test_repo',
            repo_url='https://github.com/test/repo.git',
            branch='nonexistent-branch',
            timeout=300,
        )

        assert result is False
        assert mock_run.call_count == 2
        service.logger.error.assert_called_with(
            'Failed to checkout branch nonexistent-branch: Checkout failed'
        )


@pytest.mark.asyncio
async def test_clone_or_init_git_repo_timeout(service):
    """Test git operation timeout."""
    with patch('subprocess.run') as mock_run:
        # Mock timeout exception
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=['git', 'clone'], timeout=300
        )

        result = await service.clone_or_init_git_repo(
            workspace_path='/tmp/test_repo',
            repo_url='https://github.com/test/repo.git',
            branch='main',
            timeout=300,
        )

        assert result is False
        service.logger.error.assert_called_with(
            'Git operation timed out after 300 seconds'
        )


@pytest.mark.asyncio
async def test_clone_or_init_git_repo_exception(service):
    """Test git operation with unexpected exception."""
    with patch('subprocess.run') as mock_run:
        # Mock unexpected exception
        mock_run.side_effect = Exception('Unexpected error')

        result = await service.clone_or_init_git_repo(
            workspace_path='/tmp/test_repo',
            repo_url='https://github.com/test/repo.git',
            branch='main',
            timeout=300,
        )

        assert result is False
        service.logger.error.assert_called_with(
            'Git operation failed: Unexpected error'
        )


@pytest.mark.asyncio
async def test_clone_or_init_git_repo_custom_timeout(service):
    """Test git operation with custom timeout."""
    with patch('subprocess.run') as mock_run:
        # Mock successful clone with custom timeout
        mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='Cloning...')

        result = await service.clone_or_init_git_repo(
            workspace_path='/tmp/test_repo',
            repo_url='https://github.com/test/repo.git',
            branch='main',
            timeout=600,  # Custom timeout
        )

        assert result is True
        mock_run.assert_called_once_with(
            [
                'git',
                'clone',
                '--branch',
                'main',
                'https://github.com/test/repo.git',
                '/tmp/test_repo',
            ],
            capture_output=True,
            text=True,
            timeout=600,  # Verify custom timeout is used
        )


@patch(
    'openhands.app_server.app_conversation.app_conversation_service_base.LLMSummarizingCondenser'
)
def test_create_condenser_default_agent_with_none_max_size(mock_condenser_class):
    """Test _create_condenser for DEFAULT agent with condenser_max_size = None uses default."""
    # Arrange
    mock_user_context = Mock(spec=UserContext)
    with patch.object(
        AppConversationServiceBase,
        '__abstractmethods__',
        set(),
    ):
        service = AppConversationServiceBase(
            init_git_in_empty_workspace=True,
            user_context=mock_user_context,
        )
        mock_llm = MagicMock()
        mock_llm_copy = MagicMock()
        mock_llm_copy.usage_id = 'condenser'
        mock_llm.model_copy.return_value = mock_llm_copy
        mock_condenser_instance = MagicMock()
        mock_condenser_class.return_value = mock_condenser_instance

        # Act
        service._create_condenser(mock_llm, AgentType.DEFAULT, None)

        # Assert
        mock_condenser_class.assert_called_once()
        call_kwargs = mock_condenser_class.call_args[1]
        # When condenser_max_size is None, max_size should not be passed (uses SDK default of 240)
        assert 'max_size' not in call_kwargs
        # keep_first is never passed (uses SDK default of 2)
        assert 'keep_first' not in call_kwargs
        assert call_kwargs['llm'].usage_id == 'condenser'
        mock_llm.model_copy.assert_called_once()


@patch(
    'openhands.app_server.app_conversation.app_conversation_service_base.LLMSummarizingCondenser'
)
def test_create_condenser_default_agent_with_custom_max_size(mock_condenser_class):
    """Test _create_condenser for DEFAULT agent with custom condenser_max_size."""
    # Arrange
    mock_user_context = Mock(spec=UserContext)
    with patch.object(
        AppConversationServiceBase,
        '__abstractmethods__',
        set(),
    ):
        service = AppConversationServiceBase(
            init_git_in_empty_workspace=True,
            user_context=mock_user_context,
        )
        mock_llm = MagicMock()
        mock_llm_copy = MagicMock()
        mock_llm_copy.usage_id = 'condenser'
        mock_llm.model_copy.return_value = mock_llm_copy
        mock_condenser_instance = MagicMock()
        mock_condenser_class.return_value = mock_condenser_instance

        # Act
        service._create_condenser(mock_llm, AgentType.DEFAULT, 150)

        # Assert
        mock_condenser_class.assert_called_once()
        call_kwargs = mock_condenser_class.call_args[1]
        assert call_kwargs['max_size'] == 150  # Custom value should be used
        # keep_first is never passed (uses SDK default of 2)
        assert 'keep_first' not in call_kwargs
        assert call_kwargs['llm'].usage_id == 'condenser'
        mock_llm.model_copy.assert_called_once()


@patch(
    'openhands.app_server.app_conversation.app_conversation_service_base.LLMSummarizingCondenser'
)
def test_create_condenser_plan_agent_with_none_max_size(mock_condenser_class):
    """Test _create_condenser for PLAN agent with condenser_max_size = None uses default."""
    # Arrange
    mock_user_context = Mock(spec=UserContext)
    with patch.object(
        AppConversationServiceBase,
        '__abstractmethods__',
        set(),
    ):
        service = AppConversationServiceBase(
            init_git_in_empty_workspace=True,
            user_context=mock_user_context,
        )
        mock_llm = MagicMock()
        mock_llm_copy = MagicMock()
        mock_llm_copy.usage_id = 'planning_condenser'
        mock_llm.model_copy.return_value = mock_llm_copy
        mock_condenser_instance = MagicMock()
        mock_condenser_class.return_value = mock_condenser_instance

        # Act
        service._create_condenser(mock_llm, AgentType.PLAN, None)

        # Assert
        mock_condenser_class.assert_called_once()
        call_kwargs = mock_condenser_class.call_args[1]
        # When condenser_max_size is None, max_size should not be passed (uses SDK default of 240)
        assert 'max_size' not in call_kwargs
        # keep_first is never passed (uses SDK default of 2)
        assert 'keep_first' not in call_kwargs
        assert call_kwargs['llm'].usage_id == 'planning_condenser'
        mock_llm.model_copy.assert_called_once()


@patch(
    'openhands.app_server.app_conversation.app_conversation_service_base.LLMSummarizingCondenser'
)
def test_create_condenser_plan_agent_with_custom_max_size(mock_condenser_class):
    """Test _create_condenser for PLAN agent with custom condenser_max_size."""
    # Arrange
    mock_user_context = Mock(spec=UserContext)
    with patch.object(
        AppConversationServiceBase,
        '__abstractmethods__',
        set(),
    ):
        service = AppConversationServiceBase(
            init_git_in_empty_workspace=True,
            user_context=mock_user_context,
        )
        mock_llm = MagicMock()
        mock_llm_copy = MagicMock()
        mock_llm_copy.usage_id = 'planning_condenser'
        mock_llm.model_copy.return_value = mock_llm_copy
        mock_condenser_instance = MagicMock()
        mock_condenser_class.return_value = mock_condenser_instance

        # Act
        service._create_condenser(mock_llm, AgentType.PLAN, 200)

        # Assert
        mock_condenser_class.assert_called_once()
        call_kwargs = mock_condenser_class.call_args[1]
        assert call_kwargs['max_size'] == 200  # Custom value should be used
        # keep_first is never passed (uses SDK default of 2)
        assert 'keep_first' not in call_kwargs
        assert call_kwargs['llm'].usage_id == 'planning_condenser'
        mock_llm.model_copy.assert_called_once()


# =============================================================================
# Tests for security analyzer helpers
# =============================================================================


@pytest.mark.parametrize('value', [None, '', 'none', 'NoNe'])
def test_create_security_analyzer_returns_none_for_empty_values(value):
    """_create_security_analyzer_from_string returns None for empty/none values."""
    # Arrange
    service, _ = _create_service_with_mock_user_context(
        MockUserInfo(), bind_methods=('_create_security_analyzer_from_string',)
    )

    # Act
    result = service._create_security_analyzer_from_string(value)

    # Assert
    assert result is None


def test_create_security_analyzer_returns_llm_analyzer():
    """_create_security_analyzer_from_string returns LLMSecurityAnalyzer for llm string."""
    # Arrange
    security_analyzer_str = 'llm'
    service, _ = _create_service_with_mock_user_context(
        MockUserInfo(), bind_methods=('_create_security_analyzer_from_string',)
    )

    # Act
    result = service._create_security_analyzer_from_string(security_analyzer_str)

    # Assert
    from openhands.sdk.security import LLMSecurityAnalyzer

    assert isinstance(result, LLMSecurityAnalyzer)


def test_create_security_analyzer_logs_warning_for_unknown_value():
    """_create_security_analyzer_from_string logs warning and returns None for unknown."""
    # Arrange
    unknown_value = 'custom'
    service, _ = _create_service_with_mock_user_context(
        MockUserInfo(), bind_methods=('_create_security_analyzer_from_string',)
    )

    # Act
    with patch(
        'openhands.app_server.app_conversation.app_conversation_service_base._logger'
    ) as mock_logger:
        result = service._create_security_analyzer_from_string(unknown_value)

    # Assert
    assert result is None
    mock_logger.warning.assert_called_once()


def test_select_confirmation_policy_when_disabled_returns_never_confirm():
    """_select_confirmation_policy returns NeverConfirm when confirmation_mode is False."""
    # Arrange
    confirmation_mode = False
    security_analyzer = 'llm'
    service, _ = _create_service_with_mock_user_context(
        MockUserInfo(), bind_methods=('_select_confirmation_policy',)
    )

    # Act
    policy = service._select_confirmation_policy(confirmation_mode, security_analyzer)

    # Assert
    from openhands.sdk.security import NeverConfirm

    assert isinstance(policy, NeverConfirm)


def test_select_confirmation_policy_llm_returns_confirm_risky():
    """_select_confirmation_policy uses ConfirmRisky when analyzer is llm."""
    # Arrange
    confirmation_mode = True
    security_analyzer = 'llm'
    service, _ = _create_service_with_mock_user_context(
        MockUserInfo(), bind_methods=('_select_confirmation_policy',)
    )

    # Act
    policy = service._select_confirmation_policy(confirmation_mode, security_analyzer)

    # Assert
    from openhands.sdk.security import ConfirmRisky

    assert isinstance(policy, ConfirmRisky)


@pytest.mark.parametrize('security_analyzer', [None, '', 'none', 'custom'])
def test_select_confirmation_policy_non_llm_returns_always_confirm(
    security_analyzer,
):
    """_select_confirmation_policy falls back to AlwaysConfirm for non-llm values."""
    # Arrange
    confirmation_mode = True
    service, _ = _create_service_with_mock_user_context(
        MockUserInfo(), bind_methods=('_select_confirmation_policy',)
    )

    # Act
    policy = service._select_confirmation_policy(confirmation_mode, security_analyzer)

    # Assert
    from openhands.sdk.security import AlwaysConfirm

    assert isinstance(policy, AlwaysConfirm)


@pytest.mark.asyncio
async def test_set_security_analyzer_skips_when_no_session_key():
    """_set_security_analyzer_from_settings exits early without session_api_key."""
    # Arrange
    agent_server_url = 'https://agent.example.com'
    conversation_id = uuid4()
    httpx_client = AsyncMock()
    service, _ = _create_service_with_mock_user_context(
        MockUserInfo(),
        bind_methods=(
            '_create_security_analyzer_from_string',
            '_set_security_analyzer_from_settings',
        ),
    )

    with patch.object(service, '_create_security_analyzer_from_string') as mock_create:
        # Act
        await service._set_security_analyzer_from_settings(
            agent_server_url=agent_server_url,
            session_api_key=None,
            conversation_id=conversation_id,
            security_analyzer_str='llm',
            httpx_client=httpx_client,
        )

    # Assert
    mock_create.assert_not_called()
    httpx_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_set_security_analyzer_skips_when_analyzer_none():
    """_set_security_analyzer_from_settings skips API call when analyzer resolves to None."""
    # Arrange
    agent_server_url = 'https://agent.example.com'
    session_api_key = 'session-key'
    conversation_id = uuid4()
    httpx_client = AsyncMock()
    service, _ = _create_service_with_mock_user_context(
        MockUserInfo(),
        bind_methods=(
            '_create_security_analyzer_from_string',
            '_set_security_analyzer_from_settings',
        ),
    )

    with patch.object(
        service, '_create_security_analyzer_from_string', return_value=None
    ) as mock_create:
        # Act
        await service._set_security_analyzer_from_settings(
            agent_server_url=agent_server_url,
            session_api_key=session_api_key,
            conversation_id=conversation_id,
            security_analyzer_str='none',
            httpx_client=httpx_client,
        )

    # Assert
    mock_create.assert_called_once_with('none')
    httpx_client.post.assert_not_called()


class DummyAnalyzer:
    """Simple analyzer stub for testing model_dump contract."""

    def __init__(self, payload: dict):
        self._payload = payload

    def model_dump(self) -> dict:
        return self._payload


@pytest.mark.asyncio
async def test_set_security_analyzer_successfully_calls_agent_server():
    """_set_security_analyzer_from_settings posts analyzer payload when available."""
    # Arrange
    agent_server_url = 'https://agent.example.com'
    session_api_key = 'session-key'
    conversation_id = uuid4()
    analyzer_payload = {'type': 'llm'}
    httpx_client = AsyncMock()
    http_response = MagicMock()
    http_response.raise_for_status = MagicMock()
    httpx_client.post.return_value = http_response
    service, _ = _create_service_with_mock_user_context(
        MockUserInfo(),
        bind_methods=(
            '_create_security_analyzer_from_string',
            '_set_security_analyzer_from_settings',
        ),
    )

    analyzer = DummyAnalyzer(analyzer_payload)

    with (
        patch.object(
            service,
            '_create_security_analyzer_from_string',
            return_value=analyzer,
        ) as mock_create,
        patch(
            'openhands.app_server.app_conversation.app_conversation_service_base._logger'
        ) as mock_logger,
    ):
        # Act
        await service._set_security_analyzer_from_settings(
            agent_server_url=agent_server_url,
            session_api_key=session_api_key,
            conversation_id=conversation_id,
            security_analyzer_str='llm',
            httpx_client=httpx_client,
        )

    # Assert
    mock_create.assert_called_once_with('llm')
    httpx_client.post.assert_awaited_once_with(
        f'{agent_server_url}/api/conversations/{conversation_id}/security_analyzer',
        json={'security_analyzer': analyzer_payload},
        headers={'X-Session-API-Key': session_api_key},
        timeout=30.0,
    )
    http_response.raise_for_status.assert_called_once()
    mock_logger.info.assert_called()


@pytest.mark.asyncio
async def test_set_security_analyzer_logs_warning_on_failure():
    """_set_security_analyzer_from_settings warns but does not raise on errors."""
    # Arrange
    agent_server_url = 'https://agent.example.com'
    session_api_key = 'session-key'
    conversation_id = uuid4()
    analyzer_payload = {'type': 'llm'}
    httpx_client = AsyncMock()
    httpx_client.post.side_effect = RuntimeError('network down')
    service, _ = _create_service_with_mock_user_context(
        MockUserInfo(),
        bind_methods=(
            '_create_security_analyzer_from_string',
            '_set_security_analyzer_from_settings',
        ),
    )

    analyzer = DummyAnalyzer(analyzer_payload)

    with (
        patch.object(
            service,
            '_create_security_analyzer_from_string',
            return_value=analyzer,
        ) as mock_create,
        patch(
            'openhands.app_server.app_conversation.app_conversation_service_base._logger'
        ) as mock_logger,
    ):
        # Act
        await service._set_security_analyzer_from_settings(
            agent_server_url=agent_server_url,
            session_api_key=session_api_key,
            conversation_id=conversation_id,
            security_analyzer_str='llm',
            httpx_client=httpx_client,
        )

    # Assert
    mock_create.assert_called_once_with('llm')
    httpx_client.post.assert_awaited_once()
    mock_logger.warning.assert_called()


# =============================================================================
# Tests for _configure_git_user_settings
# =============================================================================


def _create_service_with_mock_user_context(
    user_info: MockUserInfo, bind_methods: tuple[str, ...] | None = None
) -> tuple:
    """Create a mock service with selected real methods bound for testing.

    Uses MagicMock for the service but binds the real method for testing.

    Returns a tuple of (service, mock_user_context) for testing.
    """
    mock_user_context = MagicMock()
    mock_user_context.get_user_info = AsyncMock(return_value=user_info)
    # No GPG key configured by default; _configure_gpg_signing is a no-op.
    mock_user_context.get_secrets = AsyncMock(return_value={})

    # Create a simple mock service and set required attribute
    service = MagicMock()
    service.user_context = mock_user_context
    methods_to_bind = ['_configure_git_user_settings', '_configure_gpg_signing']
    if bind_methods:
        methods_to_bind.extend(bind_methods)
        # Remove potential duplicates while keeping order
        methods_to_bind = list(dict.fromkeys(methods_to_bind))

    # Bind actual methods from the real class to test implementations directly
    for method_name in methods_to_bind:
        real_method = getattr(AppConversationServiceBase, method_name)
        setattr(service, method_name, MethodType(real_method, service))

    return service, mock_user_context


@pytest.fixture
def mock_workspace():
    """Create a mock workspace instance for testing."""
    return MockWorkspace(working_dir='/workspace/project')


@pytest.mark.asyncio
async def test_clone_or_init_git_repo_uses_shallow_clone_by_default(mock_workspace):
    user_info = MockUserInfo()
    service, mock_user_context = _create_service_with_mock_user_context(
        user_info,
        bind_methods=(
            'clone_or_init_git_repo',
            '_get_azure_devops_bearer_token_for_git',
        ),
    )
    service.init_git_in_empty_workspace = True
    mock_user_context.get_authenticated_git_url = AsyncMock(
        return_value='https://github.com/owner/repo.git'
    )

    task = Mock()
    task.request = Mock(
        selected_repository='owner/repo',
        selected_branch=None,
        git_provider=ProviderType.GITHUB,
    )

    await service.clone_or_init_git_repo(task, mock_workspace)

    mock_workspace.execute_command.assert_any_call(
        'git clone --depth 1 https://github.com/owner/repo.git repo',
        mock_workspace.working_dir,
        120,
    )


@pytest.mark.asyncio
async def test_clone_or_init_git_repo_shallow_clones_selected_branch(mock_workspace):
    user_info = MockUserInfo()
    service, mock_user_context = _create_service_with_mock_user_context(
        user_info,
        bind_methods=(
            'clone_or_init_git_repo',
            '_get_azure_devops_bearer_token_for_git',
        ),
    )
    service.init_git_in_empty_workspace = True
    mock_user_context.get_authenticated_git_url = AsyncMock(
        return_value='https://github.com/owner/repo.git'
    )

    task = Mock()
    task.request = Mock(
        selected_repository='owner/repo',
        selected_branch='feature-branch',
        git_provider=ProviderType.GITHUB,
    )

    await service.clone_or_init_git_repo(task, mock_workspace)

    mock_workspace.execute_command.assert_any_call(
        'git clone --depth 1 --branch feature-branch https://github.com/owner/repo.git repo',
        mock_workspace.working_dir,
        120,
    )


@pytest.mark.asyncio
async def test_clone_or_init_git_repo_preserves_full_clone_when_enabled(
    mock_workspace,
):
    user_info = MockUserInfo(git_full_clone=True)
    service, mock_user_context = _create_service_with_mock_user_context(
        user_info,
        bind_methods=(
            'clone_or_init_git_repo',
            '_get_azure_devops_bearer_token_for_git',
        ),
    )
    service.init_git_in_empty_workspace = True
    mock_user_context.get_authenticated_git_url = AsyncMock(
        return_value='https://github.com/owner/repo.git'
    )

    task = Mock()
    task.request = Mock(
        selected_repository='owner/repo',
        selected_branch='feature-branch',
        git_provider=ProviderType.GITHUB,
    )

    await service.clone_or_init_git_repo(task, mock_workspace)

    mock_workspace.execute_command.assert_any_call(
        'git clone https://github.com/owner/repo.git repo',
        mock_workspace.working_dir,
        120,
    )
    commands = [call.args[0] for call in mock_workspace.execute_command.call_args_list]
    assert not any('git clone --depth 1' in command for command in commands)


@pytest.mark.asyncio
async def test_clone_or_init_git_repo_quotes_selected_branch_before_checkout(
    mock_workspace,
):
    user_info = MockUserInfo()
    service, mock_user_context = _create_service_with_mock_user_context(
        user_info,
        bind_methods=(
            'clone_or_init_git_repo',
            '_get_azure_devops_bearer_token_for_git',
        ),
    )
    service.init_git_in_empty_workspace = True
    mock_user_context.get_authenticated_git_url = AsyncMock(
        return_value='https://github.com/owner/repo.git'
    )

    task = Mock()
    task.request = Mock(
        selected_repository='owner/repo',
        selected_branch='feature>tmp',
    )

    await service.clone_or_init_git_repo(task, mock_workspace)

    mock_workspace.execute_command.assert_any_call(
        "git checkout 'feature>tmp'",
        Path(mock_workspace.working_dir) / 'repo',
    )


def _workspace_with_failing_clone(
    stderr: str, repository_present: bool = False
) -> MockWorkspace:
    """A workspace whose ``git clone`` exits 128; other commands succeed.

    ``repository_present`` says whether the clone target already holds a
    checkout, as when a sub-conversation shares its parent's sandbox.
    """
    workspace = MockWorkspace(working_dir='/workspace/project')

    async def execute_command(command, cwd=None, timeout=None):
        if ' clone ' in command:
            return MockCommandResult(exit_code=128, stderr=stderr)
        if command.startswith('test -d '):
            return MockCommandResult(exit_code=0 if repository_present else 1)
        return MockCommandResult()

    workspace.execute_command = AsyncMock(side_effect=execute_command)
    return workspace


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('git_provider', 'repository', 'remote_url'),
    [
        (ProviderType.GITHUB, 'owner/repo', 'https://github.com/owner/repo.git'),
        (
            ProviderType.AZURE_DEVOPS,
            'org/project/repo',
            'https://dev.azure.com/org/project/_git/repo',
        ),
    ],
)
async def test_failed_selected_repository_clone_is_not_silent(
    git_provider, repository, remote_url
):
    """A failed clone stops setup instead of continuing on an empty workspace."""
    service, mock_user_context = _create_service_with_mock_user_context(
        MockUserInfo(),
        bind_methods=(
            'clone_or_init_git_repo',
            '_get_azure_devops_bearer_token_for_git',
        ),
    )
    service.init_git_in_empty_workspace = True
    mock_user_context.get_authenticated_git_url = AsyncMock(return_value=remote_url)
    mock_user_context.get_latest_token = AsyncMock(return_value='entra.access.token')
    workspace = _workspace_with_failing_clone(
        'fatal: Remote branch missing not found in upstream origin'
    )
    task = Mock()
    task.request = Mock(
        selected_repository=repository,
        selected_branch='missing',
        git_provider=git_provider,
    )

    with pytest.raises(ValueError, match='clone') as exc_info:
        await service.clone_or_init_git_repo(task, workspace)

    assert f"{repository!r} at branch 'missing'" in str(exc_info.value)
    commands = [call.args[0] for call in workspace.execute_command.call_args_list]
    assert any(' clone ' in command for command in commands)
    assert not any(command.startswith('git checkout') for command in commands), (
        'checkout ran in a directory the failed clone never created'
    )


@pytest.mark.asyncio
async def test_clone_into_an_existing_checkout_keeps_using_it():
    """A sub-conversation sharing its parent's sandbox reuses the parent's clone."""
    service, mock_user_context = _create_service_with_mock_user_context(
        MockUserInfo(),
        bind_methods=(
            'clone_or_init_git_repo',
            '_get_azure_devops_bearer_token_for_git',
        ),
    )
    service.init_git_in_empty_workspace = True
    mock_user_context.get_authenticated_git_url = AsyncMock(
        return_value='https://github.com/owner/repo.git'
    )
    workspace = _workspace_with_failing_clone(
        "fatal: destination path 'repo' already exists and is not an empty directory.",
        repository_present=True,
    )
    task = Mock()
    task.request = Mock(
        selected_repository='owner/repo',
        selected_branch=None,
        git_provider=ProviderType.GITHUB,
    )

    await service.clone_or_init_git_repo(task, workspace)

    checkouts = [
        call
        for call in workspace.execute_command.call_args_list
        if call.args[0].startswith('git checkout -b openhands-workspace-')
    ]
    assert len(checkouts) == 1
    assert checkouts[0].args[1] == Path('/workspace/project') / 'repo'


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which('git') is None, reason='git not available')
async def test_existing_checkout_check_runs_in_a_real_shell(tmp_path):
    """Run the produced check for real, with a repository name that needs quoting."""
    service, mock_user_context = _create_service_with_mock_user_context(
        MockUserInfo(),
        bind_methods=(
            'clone_or_init_git_repo',
            '_get_azure_devops_bearer_token_for_git',
        ),
    )
    service.init_git_in_empty_workspace = True
    mock_user_context.get_authenticated_git_url = AsyncMock(
        return_value='https://dev.azure.com/org/project/_git/My%20Repo'
    )
    mock_user_context.get_latest_token = AsyncMock(return_value=None)
    workspace = _workspace_with_failing_clone('fatal: Authentication failed')
    task = Mock()
    task.request = Mock(
        selected_repository='org/project/My Repo',
        selected_branch=None,
        git_provider=ProviderType.AZURE_DEVOPS,
    )

    with pytest.raises(ValueError):
        await service.clone_or_init_git_repo(task, workspace)

    check = next(
        call.args[0]
        for call in workspace.execute_command.call_args_list
        if call.args[0].startswith('test -d ')
    )
    env = {
        key: value for key, value in os.environ.items() if not key.startswith('GIT_')
    }
    env.update(HOME=str(tmp_path), GIT_CONFIG_NOSYSTEM='1')

    def check_exit_code() -> int:
        return subprocess.run(
            ['sh', '-c', check], cwd=tmp_path, env=env, capture_output=True
        ).returncode

    # Nothing cloned yet.
    assert check_exit_code() != 0
    # An empty repository, as a failed clone can leave behind, is not a checkout.
    repo = tmp_path / 'My Repo'
    subprocess.run(['git', 'init', '-q', str(repo)], env=env, check=True)
    assert check_exit_code() != 0
    # A repository with a commit is.
    subprocess.run(
        [
            'git',
            '-C',
            str(repo),
            '-c',
            'user.name=Test',
            '-c',
            'user.email=test@example.com',
            'commit',
            '--allow-empty',
            '-q',
            '-m',
            'initial',
        ],
        env=env,
        check=True,
    )
    assert check_exit_code() == 0


@pytest.mark.asyncio
async def test_failed_clone_error_does_not_include_credentials():
    """The error names the repository, never the token-bearing URL or git output."""
    token = 'ghp_cloneSecretValue123'
    remote_url = f'https://x-access-token:{token}@github.com/owner/repo.git'
    service, mock_user_context = _create_service_with_mock_user_context(
        MockUserInfo(),
        bind_methods=(
            'clone_or_init_git_repo',
            '_get_azure_devops_bearer_token_for_git',
        ),
    )
    service.init_git_in_empty_workspace = True
    mock_user_context.get_authenticated_git_url = AsyncMock(return_value=remote_url)
    workspace = _workspace_with_failing_clone(
        f"fatal: unable to access '{remote_url}/': "
        'The requested URL returned error: 403'
    )
    task = Mock()
    task.request = Mock(
        selected_repository='owner/repo',
        selected_branch=None,
        git_provider=ProviderType.GITHUB,
    )

    with (
        patch(
            'openhands.app_server.app_conversation.app_conversation_service_base._logger'
        ) as mock_logger,
        pytest.raises(ValueError) as exc_info,
    ):
        await service.clone_or_init_git_repo(task, workspace)

    message = str(exc_info.value)
    assert 'owner/repo' in message
    assert token not in message
    assert 'x-access-token' not in message
    assert 'unable to access' not in message
    # The server log keeps git's output for debugging, without the credential.
    logged = ' '.join(
        str(arg) for call in mock_logger.warning.call_args_list for arg in call.args
    )
    assert 'unable to access' in logged
    assert token not in logged


@pytest.mark.asyncio
async def test_failed_azure_devops_clone_error_does_not_include_bearer_token():
    """The Entra bearer token sent with the clone stays out of the error."""
    bearer_token = 'entra.access.token'
    service, mock_user_context = _create_service_with_mock_user_context(
        MockUserInfo(),
        bind_methods=(
            'clone_or_init_git_repo',
            '_get_azure_devops_bearer_token_for_git',
        ),
    )
    service.init_git_in_empty_workspace = True
    mock_user_context.get_authenticated_git_url = AsyncMock(
        return_value='https://dev.azure.com/org/project/_git/repo'
    )
    mock_user_context.get_latest_token = AsyncMock(return_value=bearer_token)
    workspace = _workspace_with_failing_clone('fatal: Authentication failed')
    task = Mock()
    task.request = Mock(
        selected_repository='org/project/repo',
        selected_branch=None,
        git_provider=ProviderType.AZURE_DEVOPS,
    )

    with pytest.raises(ValueError) as exc_info:
        await service.clone_or_init_git_repo(task, workspace)

    clone_command = next(
        call.args[0]
        for call in workspace.execute_command.call_args_list
        if ' clone ' in call.args[0]
    )
    assert bearer_token in clone_command
    assert bearer_token not in str(exc_info.value)


def _azure_devops_provider_handler(orgs: list[str]):
    """A provider handler mock whose Azure DevOps installations are ``orgs``."""
    handler = MagicMock()
    handler.provider_tokens = {ProviderType.AZURE_DEVOPS: object()}
    handler.get_azure_devops_organizations = AsyncMock(return_value=orgs)
    return handler


@pytest.mark.asyncio
async def test_configure_azure_devops_git_credentials_writes_token_file_and_helper(
    mock_workspace,
):
    user_info = MockUserInfo()
    service, mock_user_context = _create_service_with_mock_user_context(
        user_info,
        bind_methods=('_configure_azure_devops_git_credentials',),
    )
    service.web_url = 'https://app.example.com'
    # The user belongs to a second Azure DevOps org beyond the selected repo's.
    mock_user_context.get_provider_handler = AsyncMock(
        return_value=_azure_devops_provider_handler(['org', 'org2'])
    )

    await service._configure_azure_devops_git_credentials(
        mock_workspace, 'header.payload.signature', 'org/project/repo'
    )

    commands = [call.args[0] for call in mock_workspace.execute_command.call_args_list]
    command = next(c for c in commands if 'azure-devops-credential-helper' in c)
    # The per-conversation JWT is written to a mode-600 file the helper reads.
    assert 'azure-devops-access-token' in command
    assert 'header.payload.signature' in command  # the token value is written
    assert 'chmod 600' in command
    # The helper authenticates via the webhooks secrets endpoint, reading the
    # token from the file — not the session key or a startup env var.
    assert '/api/v1/webhooks/secrets' in command
    assert 'X-Access-Token' in command
    assert 'OH_SESSION_API_KEYS_0' not in command
    assert 'SESSION_API_KEY' not in command
    assert '/api/v1/sandboxes/' not in command
    # Installed globally for every org the user belongs to.
    assert 'git config --global' in command
    assert 'credential.https://dev.azure.com/org.helper' in command
    assert 'credential.https://dev.azure.com/org2.helper' in command
    assert 'git config --local' not in command


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which('git') is None, reason='git not available')
async def test_configure_azure_devops_git_credentials_command_runs(
    mock_workspace, tmp_path
):
    """Execute the produced command for real and assert it takes effect.

    The substring assertions above mock the executor, so they cannot catch a
    command that assembles correctly but fails at runtime (e.g. a quoted
    ``$HOME`` that never expands and aborts the ``&&`` chain before any
    ``git config``). This runs the exact command in a scratch ``HOME`` and
    checks the token file and per-org git config actually land.
    """
    user_info = MockUserInfo()
    service, mock_user_context = _create_service_with_mock_user_context(
        user_info,
        bind_methods=('_configure_azure_devops_git_credentials',),
    )
    service.web_url = 'https://app.example.com'
    mock_user_context.get_provider_handler = AsyncMock(
        return_value=_azure_devops_provider_handler(['org', 'org2'])
    )

    await service._configure_azure_devops_git_credentials(
        mock_workspace, 'header.payload.signature', 'org/project/repo'
    )

    commands = [call.args[0] for call in mock_workspace.execute_command.call_args_list]
    command = next(c for c in commands if 'azure-devops-credential-helper' in c)

    env = os.environ.copy()
    env['HOME'] = str(tmp_path)
    # Mirror the sandbox: the helper setup runs from '/'.
    result = subprocess.run(
        ['sh', '-c', command],
        cwd='/',
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    token_file = tmp_path / '.openhands' / 'azure-devops-access-token'
    assert token_file.read_text() == 'header.payload.signature'
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert (tmp_path / '.openhands' / 'azure-devops-credential-helper').exists()

    # The && chain reached the git config steps for every org.
    for org in ('org', 'org2'):
        helper = subprocess.run(
            [
                'git',
                'config',
                '--global',
                '--get-all',
                f'credential.https://dev.azure.com/{org}.helper',
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        assert helper.returncode == 0
        assert 'azure-devops-credential-helper' in helper.stdout


@pytest.mark.asyncio
async def test_configure_azure_devops_git_credentials_skips_without_token(
    mock_workspace,
):
    user_info = MockUserInfo()
    service, _ = _create_service_with_mock_user_context(
        user_info,
        bind_methods=('_configure_azure_devops_git_credentials',),
    )
    service.web_url = 'https://app.example.com'

    # No token minted for this conversation → leave the sandbox untouched.
    await service._configure_azure_devops_git_credentials(
        mock_workspace, None, 'org/project/repo'
    )

    mock_workspace.execute_command.assert_not_called()


@pytest.mark.asyncio
async def test_azure_devops_git_credentials_logs_without_web_url(mock_workspace):
    user_info = MockUserInfo()
    service, mock_user_context = _create_service_with_mock_user_context(
        user_info,
        bind_methods=('_configure_azure_devops_git_credentials',),
    )
    service.web_url = None
    mock_user_context.get_provider_handler = AsyncMock(
        return_value=_azure_devops_provider_handler(['org'])
    )

    with patch(
        'openhands.app_server.app_conversation.app_conversation_service_base._logger.debug'
    ) as mock_debug:
        await service._configure_azure_devops_git_credentials(
            mock_workspace, 'header.payload.signature', 'org/project/repo'
        )

    mock_debug.assert_called_once_with(
        'Azure DevOps git credential helper has no configured web_url; '
        'it will rely on OH_WEBHOOKS_0_BASE_URL at runtime.'
    )


@pytest.mark.asyncio
async def test_configure_git_user_settings_both_name_and_email(mock_workspace):
    """Test configuring both git user name and email."""
    user_info = MockUserInfo(
        git_user_name='Test User', git_user_email='test@example.com'
    )
    service, mock_user_context = _create_service_with_mock_user_context(user_info)

    await service._configure_git_user_settings(mock_workspace)

    # Verify get_user_info was called
    mock_user_context.get_user_info.assert_called_once()

    # Verify both git config commands were executed
    assert mock_workspace.execute_command.call_count == 2

    # Check git config user.name call
    mock_workspace.execute_command.assert_any_call(
        "git config --global user.name 'Test User'", '/workspace/project'
    )

    # Check git config user.email call
    mock_workspace.execute_command.assert_any_call(
        'git config --global user.email test@example.com', '/workspace/project'
    )


def _assert_git_identity(workspace: MockWorkspace, name: str, email: str) -> None:
    """Assert exactly the user.name and user.email commands were run."""
    assert workspace.execute_command.call_count == 2
    workspace.execute_command.assert_any_call(
        f'git config --global user.name {name}', '/workspace/project'
    )
    workspace.execute_command.assert_any_call(
        f'git config --global user.email {email}', '/workspace/project'
    )


@pytest.mark.asyncio
async def test_configure_git_user_settings_only_name(mock_workspace):
    """A saved name is kept; the missing email falls back to the default."""
    user_info = MockUserInfo(git_user_name='Test User', git_user_email=None)
    service, _ = _create_service_with_mock_user_context(user_info)

    await service._configure_git_user_settings(mock_workspace)

    _assert_git_identity(mock_workspace, "'Test User'", 'openhands@all-hands.dev')


@pytest.mark.asyncio
async def test_configure_git_user_settings_only_email(mock_workspace):
    """A saved email is kept; the missing name falls back to the default."""
    user_info = MockUserInfo(git_user_name=None, git_user_email='test@example.com')
    service, _ = _create_service_with_mock_user_context(user_info)

    await service._configure_git_user_settings(mock_workspace)

    _assert_git_identity(mock_workspace, 'openhands', 'test@example.com')


@pytest.mark.asyncio
async def test_configure_git_user_settings_neither_set(mock_workspace):
    """Without a saved identity, the default is set."""
    user_info = MockUserInfo(git_user_name=None, git_user_email=None)
    service, _ = _create_service_with_mock_user_context(user_info)

    await service._configure_git_user_settings(mock_workspace)

    _assert_git_identity(mock_workspace, 'openhands', 'openhands@all-hands.dev')


@pytest.mark.asyncio
async def test_configure_git_user_settings_empty_strings(mock_workspace):
    """Empty saved values are treated as missing."""
    user_info = MockUserInfo(git_user_name='', git_user_email='')
    service, _ = _create_service_with_mock_user_context(user_info)

    await service._configure_git_user_settings(mock_workspace)

    _assert_git_identity(mock_workspace, 'openhands', 'openhands@all-hands.dev')


@pytest.mark.asyncio
async def test_configure_git_user_settings_get_user_info_fails(mock_workspace):
    """Test handling of exception when get_user_info fails."""
    user_info = MockUserInfo()
    service, mock_user_context = _create_service_with_mock_user_context(user_info)
    mock_user_context.get_user_info = AsyncMock(
        side_effect=Exception('User info error')
    )

    # Should not raise exception, just log warning
    await service._configure_git_user_settings(mock_workspace)

    # Verify no git config commands were executed
    mock_workspace.execute_command.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which('git') is None, reason='git not available')
async def test_configured_git_identity_lets_git_commit(mock_workspace, tmp_path):
    """Run the produced git config commands for real, then commit.

    Without them a fresh sandbox has no identity and the agent's first commit
    fails with "Please tell me who you are". ``user.useConfigOnly`` stands in
    for a sandbox whose identity cannot be guessed from its hostname.
    """
    service, _ = _create_service_with_mock_user_context(MockUserInfo())

    await service._configure_git_user_settings(mock_workspace)

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(('GIT_', 'EMAIL'))
    }
    env.update(
        HOME=str(tmp_path),
        XDG_CONFIG_HOME=str(tmp_path / '.config'),
        GIT_CONFIG_NOSYSTEM='1',
        GIT_CONFIG_COUNT='1',
        GIT_CONFIG_KEY_0='user.useConfigOnly',
        GIT_CONFIG_VALUE_0='true',
    )
    for call in mock_workspace.execute_command.call_args_list:
        subprocess.run(['sh', '-c', call.args[0]], env=env, check=True)

    repo = tmp_path / 'repo'
    subprocess.run(['git', 'init', '-q', str(repo)], env=env, check=True)
    commit = subprocess.run(
        ['git', 'commit', '--allow-empty', '-q', '-m', 'test'],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert commit.returncode == 0, commit.stderr
    author = subprocess.run(
        ['git', 'log', '-1', '--format=%an <%ae>'],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert author.stdout.strip() == 'openhands <openhands@all-hands.dev>'


@pytest.mark.asyncio
async def test_configure_git_user_settings_name_command_fails(mock_workspace):
    """Test handling when git config user.name command fails."""
    user_info = MockUserInfo(
        git_user_name='Test User', git_user_email='test@example.com'
    )
    service, _ = _create_service_with_mock_user_context(user_info)

    # Make the first command fail (user.name), second succeed (user.email)
    mock_workspace.execute_command = AsyncMock(
        side_effect=[
            MockCommandResult(exit_code=1, stderr='Permission denied'),
            MockCommandResult(exit_code=0),
        ]
    )

    # Should not raise exception
    await service._configure_git_user_settings(mock_workspace)

    # Verify both commands were still attempted
    assert mock_workspace.execute_command.call_count == 2


@pytest.mark.asyncio
async def test_configure_git_user_settings_email_command_fails(mock_workspace):
    """Test handling when git config user.email command fails."""
    user_info = MockUserInfo(
        git_user_name='Test User', git_user_email='test@example.com'
    )
    service, _ = _create_service_with_mock_user_context(user_info)

    # Make the first command succeed (user.name), second fail (user.email)
    mock_workspace.execute_command = AsyncMock(
        side_effect=[
            MockCommandResult(exit_code=0),
            MockCommandResult(exit_code=1, stderr='Permission denied'),
        ]
    )

    # Should not raise exception
    await service._configure_git_user_settings(mock_workspace)

    # Verify both commands were still attempted
    assert mock_workspace.execute_command.call_count == 2


@pytest.mark.asyncio
async def test_configure_git_user_settings_special_characters_in_name(mock_workspace):
    """Test git user name with special characters."""
    user_info = MockUserInfo(
        git_user_name="Test O'Brien", git_user_email='test@example.com'
    )
    service, _ = _create_service_with_mock_user_context(user_info)

    await service._configure_git_user_settings(mock_workspace)

    # Verify the name is passed with special characters
    mock_workspace.execute_command.assert_any_call(
        "git config --global user.name 'Test O'\"'\"'Brien'", '/workspace/project'
    )


# =============================================================================
# Tests for _configure_gpg_signing
# =============================================================================

_SAMPLE_ARMORED_KEY = """-----BEGIN PGP PRIVATE KEY BLOCK-----

lQVYBGqNlNsBEADGnBXafqUoQI1HvWn5CZor/n7/KdHQRFfy1jJC3AMoRTJz
dummyBase64BodyOnlyForTestsDoNotImport
-----END PGP PRIVATE KEY BLOCK-----"""

_SAMPLE_COLONS_OUTPUT = (
    'sec:-:4096:1:A81B138AAC22BA50:1787663579:1819199579::::scESC::::::23::0:\n'
    'uid:-:1787663579::Test User <test@example.com>:::::::\n'
)


class MockSecretSource:
    """Mock SecretSource for testing secret resolution."""

    def __init__(self, value: str | None):
        self._value = value

    def get_value(self) -> str | None:
        return self._value


def _create_service_for_gpg_tests(
    secrets: dict[str, MockSecretSource] | None = None,
) -> tuple:
    """Create a mock service with _configure_gpg_signing and its helpers bound.

    Returns (service, mock_user_context) with get_secrets configured.
    """
    mock_user_context = MagicMock()
    mock_user_context.get_secrets = AsyncMock(return_value=secrets or {})

    service = MagicMock()
    service.user_context = mock_user_context

    methods_to_bind = [
        '_configure_gpg_signing',
        '_find_gpg_secret',
        '_decode_key_material',
        '_parse_gpg_key_info',
        '_run_git_config',
    ]
    for method_name in methods_to_bind:
        real_method = getattr(AppConversationServiceBase, method_name)
        # Static methods must not be bound (no self); instance methods need
        # binding so `self` resolves to the mock service.
        if isinstance(
            inspect.getattr_static(AppConversationServiceBase, method_name),
            staticmethod,
        ):
            setattr(service, method_name, real_method)
        else:
            setattr(service, method_name, MethodType(real_method, service))

    return service, mock_user_context


@pytest.mark.asyncio
async def test_configure_gpg_signing_no_secret_configured(mock_workspace):
    """When no GPG secret is registered, signing stays off (no commands run)."""
    service, _ = _create_service_for_gpg_tests(secrets={})

    await service._configure_gpg_signing(mock_workspace)

    mock_workspace.execute_command.assert_not_called()


@pytest.mark.asyncio
async def test_configure_gpg_signing_empty_secret_value(mock_workspace):
    """An empty GPG secret logs a warning and runs no commands."""
    service, _ = _create_service_for_gpg_tests(
        secrets={'GPG_KEY': MockSecretSource(value='')}
    )

    await service._configure_gpg_signing(mock_workspace)

    mock_workspace.execute_command.assert_not_called()


@pytest.mark.asyncio
async def test_configure_gpg_signing_invalid_material(mock_workspace):
    """Non-armored, non-base64 material is rejected before any commands run."""
    service, _ = _create_service_for_gpg_tests(
        secrets={'GPG_KEY': MockSecretSource(value='not-a-valid-key')}
    )

    await service._configure_gpg_signing(mock_workspace)

    mock_workspace.execute_command.assert_not_called()


@pytest.mark.asyncio
async def test_configure_gpg_signing_armored_key_enables_signing(mock_workspace):
    """A valid armored private key is imported and signing is enabled."""
    service, _ = _create_service_for_gpg_tests(
        secrets={'GPG_KEY': MockSecretSource(value=_SAMPLE_ARMORED_KEY)}
    )
    # 1: write key file (ok)
    # 2: gpgconf kill agent (ok)
    # 3: gpg --import (ok)
    # 4: rm key file (ok)
    # 5: gpg --list-secret-keys (returns colons)
    # 6+: git config calls
    mock_workspace.execute_command = AsyncMock(
        side_effect=[
            MockCommandResult(exit_code=0),  # printf > key file
            MockCommandResult(exit_code=0),  # gpgconf --kill
            MockCommandResult(exit_code=0),  # gpg --import
            MockCommandResult(exit_code=0),  # rm -f key file
            MockCommandResult(
                exit_code=0, stdout=_SAMPLE_COLONS_OUTPUT
            ),  # list secret keys
            MockCommandResult(exit_code=0),  # git config commit.gpgsign
            MockCommandResult(exit_code=0),  # git config user.signingkey
            MockCommandResult(exit_code=0),  # git config user.email
            MockCommandResult(exit_code=0),  # git config user.name
        ]
    )

    await service._configure_gpg_signing(mock_workspace)

    commands = [call.args[0] for call in mock_workspace.execute_command.call_args_list]
    assert any('printf %s' in c and '> ' in c for c in commands), (
        'key material must be written to a temp file'
    )
    assert any('gpgconf --kill gpg-agent' in c for c in commands), (
        'gpg-agent must be reloaded before import'
    )
    assert any(c.startswith('gpg --batch --import') for c in commands), (
        'key must be imported'
    )
    assert any('rm -f /tmp/.oh-gpg-import-' in c for c in commands), (
        'temp key file must be removed'
    )
    assert any(
        'gpg --list-secret-keys --keyid-format=long --with-colons' in c
        for c in commands
    ), 'must list secret keys to extract key id'
    assert any('git config --global commit.gpgsign true' in c for c in commands), (
        'signing must be enabled'
    )
    assert any(
        'git config --global user.signingkey A81B138AAC22BA50' in c for c in commands
    ), 'signing key must be pinned'
    assert any(
        'git config --global user.email test@example.com' in c for c in commands
    ), 'email must be pinned to key UID'
    assert any(
        'git config --global user.name' in c and 'Test User' in c for c in commands
    ), 'name must be pinned to key UID'


@pytest.mark.asyncio
async def test_configure_gpg_signing_base64_encoded_key(mock_workspace):
    """A base64-encoded private key is decoded and imported."""
    import base64 as b64

    encoded = b64.b64encode(_SAMPLE_ARMORED_KEY.encode()).decode()
    service, _ = _create_service_for_gpg_tests(
        secrets={'GPG_KEY': MockSecretSource(value=encoded)}
    )
    mock_workspace.execute_command = AsyncMock(
        side_effect=[
            MockCommandResult(exit_code=0),  # write key file
            MockCommandResult(exit_code=0),  # gpgconf --kill
            MockCommandResult(exit_code=0),  # gpg --import
            MockCommandResult(exit_code=0),  # rm key file
            MockCommandResult(exit_code=0, stdout=_SAMPLE_COLONS_OUTPUT),
            MockCommandResult(exit_code=0),  # commit.gpgsign
            MockCommandResult(exit_code=0),  # user.signingkey
            MockCommandResult(exit_code=0),  # user.email
            MockCommandResult(exit_code=0),  # user.name
        ]
    )

    await service._configure_gpg_signing(mock_workspace)

    # The decoded armored key must be written to the temp file.
    write_call = mock_workspace.execute_command.call_args_list[0]
    assert 'BEGIN PGP PRIVATE KEY BLOCK' in write_call.args[0]


@pytest.mark.asyncio
async def test_configure_gpg_signing_import_fails(mock_workspace):
    """If gpg --import fails, signing is not enabled."""
    service, _ = _create_service_for_gpg_tests(
        secrets={'GPG_KEY': MockSecretSource(value=_SAMPLE_ARMORED_KEY)}
    )
    mock_workspace.execute_command = AsyncMock(
        side_effect=[
            MockCommandResult(exit_code=0),  # write key file
            MockCommandResult(exit_code=0),  # gpgconf --kill
            MockCommandResult(
                exit_code=1, stderr='import failed'
            ),  # gpg --import fails
            MockCommandResult(exit_code=0),  # rm key file (still runs in finally)
        ]
    )

    await service._configure_gpg_signing(mock_workspace)

    commands = [call.args[0] for call in mock_workspace.execute_command.call_args_list]
    # No git config commands should run after a failed import.
    assert not any('git config' in c for c in commands), (
        'no git config should run after import failure'
    )
    # Temp file still cleaned up.
    assert any('rm -f /tmp/.oh-gpg-import-' in c for c in commands)


@pytest.mark.asyncio
async def test_configure_gpg_signing_no_secret_key_imported(mock_workspace):
    """Import succeeds but no sec key (e.g. public key) -> signing disabled."""
    service, _ = _create_service_for_gpg_tests(
        secrets={'GPG_KEY': MockSecretSource(value=_SAMPLE_ARMORED_KEY)}
    )
    # list-secret-keys returns empty (no sec line).
    empty_colons = ''
    mock_workspace.execute_command = AsyncMock(
        side_effect=[
            MockCommandResult(exit_code=0),  # write key file
            MockCommandResult(exit_code=0),  # gpgconf --kill
            MockCommandResult(exit_code=0),  # gpg --import
            MockCommandResult(exit_code=0),  # rm key file
            MockCommandResult(exit_code=0, stdout=empty_colons),  # no sec keys
        ]
    )

    await service._configure_gpg_signing(mock_workspace)

    commands = [call.args[0] for call in mock_workspace.execute_command.call_args_list]
    assert not any('git config' in c for c in commands), (
        'no git config should run when no secret key is imported'
    )


@pytest.mark.asyncio
async def test_configure_gpg_signing_case_insensitive_secret_name(mock_workspace):
    """The GPG secret name is matched case-insensitively."""
    service, _ = _create_service_for_gpg_tests(
        secrets={'gpg_key': MockSecretSource(value=_SAMPLE_ARMORED_KEY)}
    )
    mock_workspace.execute_command = AsyncMock(
        side_effect=[
            MockCommandResult(exit_code=0),  # write key file
            MockCommandResult(exit_code=0),  # gpgconf --kill
            MockCommandResult(exit_code=0),  # gpg --import
            MockCommandResult(exit_code=0),  # rm key file
            MockCommandResult(exit_code=0, stdout=_SAMPLE_COLONS_OUTPUT),
            MockCommandResult(exit_code=0),  # commit.gpgsign
            MockCommandResult(exit_code=0),  # user.signingkey
            MockCommandResult(exit_code=0),  # user.email
            MockCommandResult(exit_code=0),  # user.name
        ]
    )

    await service._configure_gpg_signing(mock_workspace)

    assert mock_workspace.execute_command.call_count == 9


@pytest.mark.asyncio
async def test_configure_gpg_signing_accepts_gpg_private_key_name(mock_workspace):
    """GPG_PRIVATE_KEY is accepted as an alternative secret name."""
    service, _ = _create_service_for_gpg_tests(
        secrets={'GPG_PRIVATE_KEY': MockSecretSource(value=_SAMPLE_ARMORED_KEY)}
    )
    mock_workspace.execute_command = AsyncMock(
        side_effect=[
            MockCommandResult(exit_code=0),  # write key file
            MockCommandResult(exit_code=0),  # gpgconf --kill
            MockCommandResult(exit_code=0),  # gpg --import
            MockCommandResult(exit_code=0),  # rm key file
            MockCommandResult(exit_code=0, stdout=_SAMPLE_COLONS_OUTPUT),
            MockCommandResult(exit_code=0),  # commit.gpgsign
            MockCommandResult(exit_code=0),  # user.signingkey
            MockCommandResult(exit_code=0),  # user.email
            MockCommandResult(exit_code=0),  # user.name
        ]
    )

    await service._configure_gpg_signing(mock_workspace)

    assert mock_workspace.execute_command.call_count == 9


@pytest.mark.asyncio
async def test_configure_gpg_signing_temp_file_cleaned_on_exception(mock_workspace):
    """The temp key file is removed even if an exception occurs mid-import."""
    service, _ = _create_service_for_gpg_tests(
        secrets={'GPG_KEY': MockSecretSource(value=_SAMPLE_ARMORED_KEY)}
    )
    mock_workspace.execute_command = AsyncMock(
        side_effect=[
            MockCommandResult(exit_code=0),  # write key file
            MockCommandResult(exit_code=0),  # gpgconf --kill
            RuntimeError('boom'),  # gpg --import raises
            MockCommandResult(exit_code=0),  # rm key file (finally block)
        ]
    )

    # Should not raise; exception is caught by the outer try/except.
    await service._configure_gpg_signing(mock_workspace)

    commands = [call.args[0] for call in mock_workspace.execute_command.call_args_list]
    assert any('rm -f /tmp/.oh-gpg-import-' in c for c in commands), (
        'temp file must be cleaned up even on exception'
    )


def test_decode_key_material_armored():
    """Raw armored key material is returned as-is."""
    result = AppConversationServiceBase._decode_key_material(_SAMPLE_ARMORED_KEY)
    assert result == _SAMPLE_ARMORED_KEY


def test_decode_key_material_base64():
    """Base64-encoded armored key material is decoded."""
    import base64 as b64

    encoded = b64.b64encode(_SAMPLE_ARMORED_KEY.encode()).decode()
    result = AppConversationServiceBase._decode_key_material(encoded)
    assert result == _SAMPLE_ARMORED_KEY


def test_decode_key_material_invalid():
    """Non-key, non-base64 material returns None."""
    assert AppConversationServiceBase._decode_key_material('garbage') is None


def test_parse_gpg_key_info_extracts_id_and_email():
    """Key ID, email, and name are parsed from --with-colons output."""
    key_id, email, name = AppConversationServiceBase._parse_gpg_key_info(
        _SAMPLE_COLONS_OUTPUT
    )
    assert key_id == 'A81B138AAC22BA50'
    assert email == 'test@example.com'
    assert name == 'Test User'


def test_parse_gpg_key_info_empty_output():
    """Empty colons output yields all-None."""
    assert AppConversationServiceBase._parse_gpg_key_info('') == (None, None, None)


def test_parse_gpg_key_info_public_key_only():
    """pub lines (no sec) yield no key id."""
    pub_only = 'pub:-:4096:1:A81B138AAC22BA50:1787663579::::scESC::::::23::0:\n'
    key_id, email, name = AppConversationServiceBase._parse_gpg_key_info(pub_only)
    assert key_id is None
    assert email is None
    assert name is None


def test_find_gpg_secret_finds_gpg_key():
    """_find_gpg_secret locates GPG_KEY case-insensitively."""
    secrets = {
        'OTHER': MockSecretSource('x'),
        'gpg_key': MockSecretSource(_SAMPLE_ARMORED_KEY),
    }
    service, _ = _create_service_for_gpg_tests()
    result = service._find_gpg_secret(secrets)
    assert result is not None
    name, _ = result
    assert name == 'gpg_key'


def test_find_gpg_secret_returns_none_when_absent():
    """No matching secret name returns None."""
    secrets = {'GITHUB_TOKEN': MockSecretSource('x')}
    service, _ = _create_service_for_gpg_tests()
    assert service._find_gpg_secret(secrets) is None


# =============================================================================
# Tests for load_and_merge_all_skills (updated to use agent-server)
# =============================================================================


class TestMergeSkills:
    """Test _merge_skills method."""

    def test_merges_skills_with_no_duplicates(self):
        """Test merging skill lists with no duplicate names."""
        # Arrange
        mock_user_context = Mock(spec=UserContext)
        with patch.object(AppConversationServiceBase, '__abstractmethods__', set()):
            service = AppConversationServiceBase(
                init_git_in_empty_workspace=True, user_context=mock_user_context
            )

            skill1 = Mock(spec=Skill)
            skill1.name = 'skill1'
            skill2 = Mock(spec=Skill)
            skill2.name = 'skill2'
            skill3 = Mock(spec=Skill)
            skill3.name = 'skill3'

            skill_lists = [[skill1], [skill2], [skill3]]

            # Act
            result = service._merge_skills(skill_lists)

            # Assert
            assert len(result) == 3
            names = {s.name for s in result}
            assert names == {'skill1', 'skill2', 'skill3'}

    def test_merges_skills_with_duplicates_later_wins(self):
        """Test that later skill lists override earlier ones for duplicate names."""
        # Arrange
        mock_user_context = Mock(spec=UserContext)
        with patch.object(AppConversationServiceBase, '__abstractmethods__', set()):
            service = AppConversationServiceBase(
                init_git_in_empty_workspace=True, user_context=mock_user_context
            )

            skill1_v1 = Mock(spec=Skill)
            skill1_v1.name = 'skill1'
            skill1_v1.version = 'v1'

            skill1_v2 = Mock(spec=Skill)
            skill1_v2.name = 'skill1'
            skill1_v2.version = 'v2'

            skill2 = Mock(spec=Skill)
            skill2.name = 'skill2'

            skill_lists = [[skill1_v1], [skill1_v2, skill2]]

            # Act
            result = service._merge_skills(skill_lists)

            # Assert
            assert len(result) == 2
            skill1_result = next(s for s in result if s.name == 'skill1')
            assert skill1_result.version == 'v2'


class TestLoadAndMergeAllSkills:
    """Test load_and_merge_all_skills method (updated to use agent-server)."""

    @pytest.mark.asyncio
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.load_skills_from_agent_server'
    )
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.build_org_configs'
    )
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.build_sandbox_config'
    )
    async def test_loads_skills_successfully(
        self,
        mock_build_sandbox_config,
        mock_build_org_configs,
        mock_load_skills,
    ):
        """Test successfully loading skills from agent-server."""
        # Arrange
        mock_user_context = Mock(spec=UserContext)
        with patch.object(AppConversationServiceBase, '__abstractmethods__', set()):
            service = AppConversationServiceBase(
                init_git_in_empty_workspace=True, user_context=mock_user_context
            )

            mock_workspace = AsyncMock()
            mock_workspace.working_dir = '/workspace'

            from openhands.app_server.sandbox.sandbox_models import ExposedUrl

            sandbox = Mock(spec=SandboxInfo)
            exposed_url = ExposedUrl(
                name='AGENT_SERVER', url='http://localhost:8000', port=8000
            )
            sandbox.exposed_urls = [exposed_url]
            sandbox.session_api_key = 'test-api-key'

            skill1 = Mock(spec=Skill)
            skill1.name = 'skill1'
            skill2 = Mock(spec=Skill)
            skill2.name = 'skill2'

            mock_load_skills.return_value = [skill1, skill2]
            mock_build_org_configs.return_value = []
            mock_build_sandbox_config.return_value = {'exposed_urls': []}

            # Act
            result = await service.load_and_merge_all_skills(
                sandbox, 'owner/repo', '/workspace/repo', 'http://localhost:8000'
            )

            # Assert
            assert len(result) == 2
            assert result[0].name == 'skill1'
            assert result[1].name == 'skill2'
            mock_load_skills.assert_called_once()
            call_kwargs = mock_load_skills.call_args[1]
            assert call_kwargs['agent_server_url'] == 'http://localhost:8000'
            assert call_kwargs['session_api_key'] == 'test-api-key'
            assert call_kwargs['project_dir'] == '/workspace/repo'
            # Verify registered_marketplaces is passed (can be None or a list)
            assert 'registered_marketplaces' in call_kwargs

    @pytest.mark.asyncio
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.load_skills_from_agent_server'
    )
    async def test_returns_empty_list_when_no_agent_server_url(self, mock_load_skills):
        """Test returns empty list when agent-server URL is not available."""
        # Arrange
        mock_user_context = Mock(spec=UserContext)
        with patch.object(AppConversationServiceBase, '__abstractmethods__', set()):
            service = AppConversationServiceBase(
                init_git_in_empty_workspace=True, user_context=mock_user_context
            )

            AsyncMock()
            from openhands.app_server.sandbox.sandbox_models import ExposedUrl

            sandbox = Mock(spec=SandboxInfo)
            exposed_url = ExposedUrl(
                name='VSCODE', url='http://localhost:8080', port=8080
            )
            sandbox.exposed_urls = [exposed_url]

            # Act - pass empty string to simulate no agent server URL
            # This should still call load_skills_from_agent_server but it will fail
            result = await service.load_and_merge_all_skills(
                sandbox, 'owner/repo', '/workspace/repo', ''
            )

            # Assert - should return empty list when agent_server_url is empty
            assert result == []

    @pytest.mark.asyncio
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.load_skills_from_agent_server'
    )
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.build_org_configs'
    )
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.build_sandbox_config'
    )
    async def test_uses_project_dir_when_no_repository(
        self,
        mock_build_sandbox_config,
        mock_build_org_configs,
        mock_load_skills,
    ):
        """Test uses project_dir directly when no repository is selected."""
        # Arrange
        mock_user_context = Mock(spec=UserContext)
        with patch.object(AppConversationServiceBase, '__abstractmethods__', set()):
            service = AppConversationServiceBase(
                init_git_in_empty_workspace=True, user_context=mock_user_context
            )

            AsyncMock()
            from openhands.app_server.sandbox.sandbox_models import ExposedUrl

            sandbox = Mock(spec=SandboxInfo)
            exposed_url = ExposedUrl(
                name='AGENT_SERVER', url='http://localhost:8000', port=8000
            )
            sandbox.exposed_urls = [exposed_url]
            sandbox.session_api_key = 'test-key'

            mock_load_skills.return_value = []
            mock_build_org_configs.return_value = []
            mock_build_sandbox_config.return_value = None

            # Act
            await service.load_and_merge_all_skills(
                sandbox, None, '/workspace', 'http://localhost:8000'
            )

            # Assert
            call_kwargs = mock_load_skills.call_args[1]
            assert call_kwargs['project_dir'] == '/workspace'

    @pytest.mark.asyncio
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.load_skills_from_agent_server'
    )
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.build_org_configs'
    )
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.build_sandbox_config'
    )
    async def test_handles_exception_gracefully(
        self,
        mock_build_sandbox_config,
        mock_build_org_configs,
        mock_load_skills,
    ):
        """Test handles exceptions during skill loading."""
        # Arrange
        mock_user_context = Mock(spec=UserContext)
        with patch.object(AppConversationServiceBase, '__abstractmethods__', set()):
            service = AppConversationServiceBase(
                init_git_in_empty_workspace=True, user_context=mock_user_context
            )

            AsyncMock()
            from openhands.app_server.sandbox.sandbox_models import ExposedUrl

            sandbox = Mock(spec=SandboxInfo)
            exposed_url = ExposedUrl(
                name='AGENT_SERVER', url='http://localhost:8000', port=8000
            )
            sandbox.exposed_urls = [exposed_url]
            sandbox.session_api_key = 'test-key'

            mock_load_skills.side_effect = Exception('Network error')

            # Act
            result = await service.load_and_merge_all_skills(
                sandbox, 'owner/repo', '/workspace/repo', 'http://localhost:8000'
            )

            # Assert
            assert result == []

    @pytest.mark.asyncio
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.load_skills_from_agent_server'
    )
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.build_org_configs'
    )
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.build_sandbox_config'
    )
    async def test_sends_authenticated_marketplace_sources_to_agent_server(
        self,
        mock_build_sandbox_config,
        mock_build_org_configs,
        mock_load_skills,
    ):
        """Auto-load marketplace sources reach the agent-server as token URLs."""
        # Arrange
        from openhands.app_server.settings.settings_models import (
            MarketplaceRegistration,
        )

        authenticated_url = 'https://x-access-token:tok@github.com/o/private.git'
        mock_user_context = Mock(spec=UserContext)
        mock_user_context.get_provider_tokens = AsyncMock(
            return_value={ProviderType.GITHUB: ProviderToken(token=SecretStr('tok'))}
        )
        mock_user_context.get_user_id = AsyncMock(return_value='user-1')
        with (
            patch.object(AppConversationServiceBase, '__abstractmethods__', set()),
            patch.object(
                ProviderHandler,
                'get_authenticated_git_url',
                return_value=authenticated_url,
            ),
        ):
            service = AppConversationServiceBase(
                init_git_in_empty_workspace=True, user_context=mock_user_context
            )

            sandbox = Mock(spec=SandboxInfo)
            sandbox.exposed_urls = []
            sandbox.session_api_key = 'test-key'

            mock_load_skills.return_value = []
            mock_build_org_configs.return_value = []
            mock_build_sandbox_config.return_value = None

            marketplaces = [
                MarketplaceRegistration(
                    name='private', source='github:o/private', auto_load=True
                )
            ]

            # Act
            await service.load_and_merge_all_skills(
                sandbox,
                'owner/repo',
                '/workspace/repo',
                'http://localhost:8000',
                registered_marketplaces=marketplaces,
            )

            # Assert
            sent = mock_load_skills.call_args.kwargs['registered_marketplaces']
            assert [reg.source for reg in sent] == [authenticated_url]


class TestMarketplaceGitCredentials:
    """Entra-authenticated Azure marketplaces get a token-free credential helper."""

    @staticmethod
    def _service(token: str | None):
        mock_user_context = Mock(spec=UserContext)
        mock_user_context.get_provider_tokens = AsyncMock(
            return_value={ProviderType.AZURE_DEVOPS: ProviderToken()}
        )
        mock_user_context.get_latest_token = AsyncMock(return_value=token)
        with patch.object(AppConversationServiceBase, '__abstractmethods__', set()):
            service = AppConversationServiceBase(
                init_git_in_empty_workspace=True, user_context=mock_user_context
            )
        sandbox = Mock(spec=SandboxInfo)
        sandbox.id = 'sandbox-123'
        sandbox.exposed_urls = []
        sandbox.session_api_key = 'session-key'
        marketplaces = [
            MarketplaceRegistration(
                name='azure',
                source='https://acme@dev.azure.com/acme/Web%20Apps/_git/skills',
                auto_load=True,
            )
        ]
        return service, sandbox, marketplaces

    @pytest.mark.asyncio
    async def test_installs_global_helper_for_entra_token(self):
        """The helper is configured per org and the token never enters the command."""
        token = 'header.payload.signature'
        service, sandbox, marketplaces = self._service(token)
        workspace = AsyncMock()
        workspace.execute_command.return_value = Mock(exit_code=0, stderr='')

        with patch(
            'openhands.app_server.app_conversation.app_conversation_service_base.AsyncRemoteWorkspace',
            return_value=workspace,
        ):
            await service._configure_marketplace_git_credentials(
                sandbox, 'http://agent:8000', '/workspace', marketplaces
            )

        command = workspace.execute_command.call_args.args[0]
        assert token not in command
        # The helper reads the per-conversation token file and exchanges it at
        # the webhooks secrets endpoint, not the sandbox session key. The
        # marketplace path installs the helper but does not write the token file
        # (the per-conversation setup flow does), so the token never appears here.
        assert '/api/v1/webhooks/secrets' in command
        assert 'azure-devops-access-token' in command
        assert 'X-Access-Token' in command
        assert 'OH_SESSION_API_KEYS_0' not in command
        assert '/api/v1/sandboxes/' not in command
        assert (
            'git config --global credential.https://dev.azure.com/acme.username oauth2'
            in command
        )
        assert 'credential.https://dev.azure.com/acme.helper' in command
        assert (
            'git config --global credential.https://dev.azure.com.useHttpPath true'
            in command
        )
        workspace.reset_client.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_helper_for_pat(self):
        """PATs are embedded in the authenticated URL; no helper is installed."""
        service, sandbox, marketplaces = self._service('plain-pat')

        with patch(
            'openhands.app_server.app_conversation.app_conversation_service_base.AsyncRemoteWorkspace'
        ) as workspace_cls:
            await service._configure_marketplace_git_credentials(
                sandbox, 'http://agent:8000', '/workspace', marketplaces
            )

        workspace_cls.assert_not_called()

    @pytest.mark.asyncio
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.load_skills_from_agent_server'
    )
    @patch(
        'openhands.app_server.app_conversation.app_conversation_service_base.build_org_configs'
    )
    async def test_load_and_merge_configures_credentials_before_loading(
        self, mock_build_org_configs, mock_load_skills
    ):
        """Skill loading installs the helper before the agent-server clones."""
        service, sandbox, marketplaces = self._service('header.payload.signature')
        mock_build_org_configs.return_value = []
        mock_load_skills.return_value = []
        order: list[str] = []
        service._configure_marketplace_git_credentials = AsyncMock(
            side_effect=lambda *a, **k: order.append('credentials')
        )
        mock_load_skills.side_effect = lambda **k: order.append('load') or []

        await service.load_and_merge_all_skills(
            sandbox,
            None,
            '/workspace',
            'http://agent:8000',
            registered_marketplaces=marketplaces,
        )

        assert order == ['credentials', 'load']


class TestLoadSkillsAndUpdateAgent:
    """_load_skills_and_update_agent threads marketplaces into skill loading."""

    @pytest.mark.asyncio
    async def test_forwards_registered_marketplaces(self):
        """Registered marketplaces reach load_and_merge_all_skills at startup."""
        # Arrange
        from openhands.app_server.settings.settings_models import (
            MarketplaceRegistration,
        )

        mock_user_context = Mock(spec=UserContext)
        with patch.object(AppConversationServiceBase, '__abstractmethods__', set()):
            service = AppConversationServiceBase(
                init_git_in_empty_workspace=True, user_context=mock_user_context
            )
            service.load_and_merge_all_skills = AsyncMock(return_value=[])
            service._create_agent_with_skills = Mock(return_value='updated-agent')

            remote_workspace = AsyncMock()
            remote_workspace.host = 'http://agent:8000'
            marketplaces = [
                MarketplaceRegistration(name='team', source='github:o/team')
            ]

            # Act
            result = await service._load_skills_and_update_agent(
                Mock(spec=SandboxInfo),
                Mock(),
                remote_workspace,
                'owner/repo',
                '/workspace/repo',
                registered_marketplaces=marketplaces,
            )

            # Assert
            assert result == 'updated-agent'
            forwarded = service.load_and_merge_all_skills.call_args.kwargs[
                'registered_marketplaces'
            ]
            assert forwarded == marketplaces


def test_warns_for_auto_load_marketplace_read_as_sandbox_path():
    """Auto-load sources the agent-server will not clone are logged."""
    from openhands.app_server.app_conversation.app_conversation_service_base import (
        _warn_unresolved_marketplace_sources,
    )
    from openhands.app_server.settings.settings_models import (
        MarketplaceRegistration,
    )

    registrations = [
        MarketplaceRegistration(name='bare', source='owner/repo', auto_load=True),
        MarketplaceRegistration(name='off', source='owner/other', auto_load=False),
        MarketplaceRegistration(name='gh', source='github:o/r', auto_load=True),
        MarketplaceRegistration(
            name='url', source='https://github.com/o/r.git', auto_load=True
        ),
        MarketplaceRegistration(
            name='scp', source='git@gitlab.example.com:o/r.git', auto_load=True
        ),
    ]

    with patch(
        'openhands.app_server.app_conversation.app_conversation_service_base._logger'
    ) as logger:
        _warn_unresolved_marketplace_sources(registrations)

    assert [c.args[1:] for c in logger.warning.call_args_list] == [
        ('bare', 'owner/repo')
    ]


# ---------------------------------------------------------------------------
# Workstream A: provider-correct clone URLs and a session-key-free credential
# helper (Azure DevOps git parity for Microsoft Entra sign-in).
# ---------------------------------------------------------------------------


def _provider_resolution_service(user_context) -> MagicMock:
    service = MagicMock()
    service.user_context = user_context
    service.init_git_in_empty_workspace = True
    for name in (
        'clone_or_init_git_repo',
        '_get_azure_devops_bearer_token_for_git',
        '_configure_git_user_settings',
        '_configure_gpg_signing',
    ):
        setattr(
            service,
            name,
            MethodType(getattr(AppConversationServiceBase, name), service),
        )
    return service


@pytest.mark.asyncio
async def test_clone_url_uses_the_requested_git_provider():
    """A same-named repository on another provider must not win URL resolution."""
    entra_token = 'header.payload.signature'
    handler = ProviderHandler(
        provider_tokens=MappingProxyType(
            {
                ProviderType.GITLAB: ProviderToken(
                    token=SecretStr('glpat-token'), host='gitlab.example.com'
                ),
                ProviderType.AZURE_DEVOPS: ProviderToken(
                    token=SecretStr(entra_token), host='dev.azure.com/contoso'
                ),
            }
        )
    )

    def service_for(provider):
        provider_service = Mock()
        provider_service.get_repository_details_from_repo_name = AsyncMock(
            return_value=Repository(
                id='1',
                full_name='contoso/web/app',
                git_provider=provider,
                is_public=False,
            )
        )
        return provider_service

    user_context = MagicMock()
    user_context.get_user_info = AsyncMock(
        return_value=MockUserInfo(git_full_clone=False)
    )
    user_context.get_secrets = AsyncMock(return_value={})
    user_context.get_provider_handler = AsyncMock(return_value=handler)
    user_context.get_latest_token = AsyncMock(return_value=entra_token)
    user_context.get_authenticated_git_url = MethodType(
        AuthUserContext.get_authenticated_git_url, user_context
    )

    workspace = MockWorkspace(working_dir='/workspace/project')
    task = Mock()
    task.request = Mock(
        selected_repository='contoso/web/app',
        selected_branch='main',
        git_provider=ProviderType.AZURE_DEVOPS,
    )

    with patch.object(ProviderHandler, 'get_service', side_effect=service_for):
        service = _provider_resolution_service(user_context)
        await service.clone_or_init_git_repo(task, workspace)

    clone = next(
        call.args[0]
        for call in workspace.execute_command.call_args_list
        if ' clone ' in call.args[0]
    )
    assert 'https://dev.azure.com/contoso/web/_git/app' in clone
    assert 'gitlab.example.com' not in clone


def test_azure_devops_credential_helper_script_auth_source():
    """The helper reads the JWT from the per-conversation file, not the env/key."""
    script = _azure_devops_credential_helper_script('https://app.example.com')

    # Reads the per-conversation token file and exchanges it at the webhooks
    # secrets endpoint.
    assert 'azure-devops-access-token' in script
    assert '/api/v1/webhooks/secrets' in script
    assert 'X-Access-Token' in script

    # Does not read the token from an environment variable, nor depend on the
    # session key or the old sandbox-settings path.
    assert 'OH_AZURE_DEVOPS_SECRETS_ACCESS_TOKEN' not in script
    assert 'OH_SESSION_API_KEYS_0' not in script
    assert 'SESSION_API_KEY' not in script
    assert 'X-Session-API-Key' not in script
    assert '/api/v1/sandboxes/' not in script
