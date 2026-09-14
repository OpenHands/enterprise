"""Separate public app links from app callbacks consumed inside sandboxes."""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.env_parser import from_env
from openhands.agent_server.models import StartConversationRequest
from openhands.app_server.app_conversation.conversation_secret_enricher import (
    ConversationSecretEnrichment,
)
from openhands.app_server.config import AppServerConfig
from openhands.app_server.integrations.provider import ProviderToken, ProviderType
from openhands.app_server.sandbox.remote_sandbox_service import RemoteSandboxService
from openhands.app_server.sandbox.sandbox_models import SandboxInfo, SandboxStatus
from openhands.app_server.sandbox.sandbox_service import (
    ALLOW_CORS_ORIGINS_VARIABLE,
    WEBHOOK_CALLBACK_VARIABLE,
)
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.user.user_models import UserInfo
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.secret import LookupSecret, StaticSecret
from openhands.sdk.workspace.remote.async_remote_workspace import AsyncRemoteWorkspace
from tests.unit.app_server.fixture_assertions import present
from tests.unit.app_server.service_mock_fixtures import (
    MockedConversationService,
    make_conversation_service,
)
from tests.unit.app_server.user_info_fixtures import make_user_info

PUBLIC_URL = 'http://localhost:13000'
INTERNAL_URL = 'http://openhands:3000'


@pytest.mark.parametrize('internal', [None, INTERNAL_URL + '/'])
def test_env_callback_base_preserves_public_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, internal: str | None
) -> None:
    monkeypatch.setenv('OH_PERSISTENCE_DIR', str(tmp_path))
    monkeypatch.setenv('OH_WEB_URL', PUBLIC_URL)
    monkeypatch.delenv('OH_SANDBOX_CALLBACK_URL', raising=False)
    if internal:
        monkeypatch.setenv('OH_SANDBOX_CALLBACK_URL', internal)
    config = AppServerConfig.model_validate(from_env(AppServerConfig, 'OH'))
    assert config.web_url == PUBLIC_URL
    assert config.get_sandbox_callback_url() == (
        INTERNAL_URL if internal else PUBLIC_URL
    )


@dataclass
class ConversationFixture:
    service: MockedConversationService
    mock_user_context: AsyncMock
    mock_jwt_service: MagicMock
    mock_user: UserInfo
    mock_sandbox: SandboxInfo


@pytest.fixture
def conversation() -> ConversationFixture:
    service = make_conversation_service()
    service.app_mode = 'test'
    service.user_context.get_user_email.return_value = None
    service.user_context.get_user_id.return_value = 'test-user'
    fixture = ConversationFixture(
        service=service,
        mock_user_context=service.user_context,
        mock_jwt_service=service.jwt_service,
        mock_user=make_user_info(id='test-user', llm_api_key='test-api-key'),
        mock_sandbox=SandboxInfo(
            id='sandbox',
            created_by_user_id='test-user',
            sandbox_spec_id='spec',
            status=SandboxStatus.RUNNING,
            session_api_key='session-key',
        ),
    )
    fixture.service.web_url = PUBLIC_URL
    fixture.service.sandbox_callback_url = INTERNAL_URL
    fixture.mock_user_context.get_mcp_api_key = AsyncMock(return_value='mcp-test-key')
    fixture.mock_jwt_service.create_jws_token.return_value = 'signed-lookup-test-token'
    fixture.mock_user_context.get_secrets.return_value = {
        'CUSTOM_SECRET': StaticSecret(value=SecretStr('custom-test-secret'))
    }
    fixture.mock_user_context.get_provider_tokens = AsyncMock(
        return_value={
            ProviderType.GITHUB: ProviderToken(token=SecretStr('github-test-token')),
        }
    )
    return fixture


@pytest.mark.parametrize('internal', [None, INTERNAL_URL])
async def test_git_integration_and_mcp_callbacks_use_internal_base(
    conversation: ConversationFixture, internal: str | None
) -> None:
    fixture = conversation
    fixture.service.sandbox_callback_url = internal
    expected = internal or PUBLIC_URL
    enrich = AsyncMock(return_value=ConversationSecretEnrichment())
    fixture.service.conversation_secret_enricher = Mock(enrich=enrich)
    secrets, _ = await fixture.service._setup_conversation_secrets(
        fixture.mock_user, None, None
    )
    assert isinstance(secrets['GITHUB_TOKEN'], LookupSecret)
    assert secrets['GITHUB_TOKEN'].url == expected + '/api/v1/webhooks/secrets'
    assert secrets['GITHUB_TOKEN'].headers == {
        'X-Access-Token': 'signed-lookup-test-token'
    }
    assert isinstance(secrets['CUSTOM_SECRET'], StaticSecret)
    assert (
        present(secrets['CUSTOM_SECRET'].value).get_secret_value()
        == 'custom-test-secret'
    )
    assert present(enrich.await_args).kwargs['web_url'] == expected
    servers: dict[str, MCPServer] = {}
    await fixture.service._add_system_mcp_servers(servers, uuid4())
    assert servers['default'].url == expected + '/mcp/mcp'
    assert (
        present(servers['default'].headers)['X-Session-API-Key'].get_secret_value()
        == 'mcp-test-key'
    )
    assert fixture.service.web_url == PUBLIC_URL


async def test_conversation_host_context_stays_public(
    conversation: ConversationFixture,
) -> None:
    fixture = conversation
    with patch(
        'openhands.app_server.app_conversation.live_status_app_conversation_service.get_default_tools',
        return_value=[],
    ):
        result: StartConversationRequest = (
            await fixture.service._build_start_conversation_request_for_user(
                user=fixture.mock_user,
                sandbox=fixture.mock_sandbox,
                conversation_id=uuid4(),
                initial_message=None,
                system_message_suffix=None,
                git_provider=None,
                working_dir='/workspace/project',
                remote_workspace=None,
            )
        )
    suffix = present(present(result.agent.agent_context).system_message_suffix)
    assert f'<HOST>\n{PUBLIC_URL}\n</HOST>' in suffix
    assert INTERNAL_URL not in suffix
    git_secret = present(result.secrets)['GITHUB_TOKEN']
    assert isinstance(git_secret, LookupSecret)
    assert git_secret.url.startswith(INTERNAL_URL)
    assert result.agent.mcp_config['default'].url == INTERNAL_URL + '/mcp/mcp'


async def test_azure_git_helper_uses_internal_secret_route(
    conversation: ConversationFixture,
) -> None:
    fixture = conversation
    workspace = AsyncMock(spec=AsyncRemoteWorkspace)
    sandbox = SandboxInfo(
        id='sandbox-1',
        created_by_user_id='user-1',
        sandbox_spec_id='spec',
        status=SandboxStatus.RUNNING,
        session_api_key='sandbox-test-key',
    )
    await fixture.service._configure_azure_devops_git_credential_helper(
        workspace,
        Path('/workspace/project'),
        'org/project/repo',
        sandbox,
    )
    command = workspace.execute_command.await_args.args[0]
    assert INTERNAL_URL in command
    assert PUBLIC_URL not in command
    assert '/api/v1/sandboxes/sandbox-1/settings/secrets/azure_devops_token' in command


@pytest.mark.parametrize('internal', [None, INTERNAL_URL])
async def test_remote_webhook_callback_keeps_public_cors(internal: str | None) -> None:
    service = RemoteSandboxService(
        sandbox_spec_service=Mock(),
        api_url='http://runtime-api',
        api_key='test',
        web_url=PUBLIC_URL,
        resource_factor=1,
        runtime_class=None,
        start_sandbox_timeout=30,
        max_num_sandboxes=5,
        user_context=Mock(),
        httpx_client=AsyncMock(),
        db_session=AsyncMock(),
        sandbox_callback_url=internal,
    )
    environment = await service._init_environment(
        SandboxSpecInfo(
            id='image', command=None, initial_env={}, working_dir='/workspace'
        ),
        'sandbox',
    )
    assert (
        environment[WEBHOOK_CALLBACK_VARIABLE]
        == (internal or PUBLIC_URL) + '/api/v1/webhooks'
    )
    assert environment[ALLOW_CORS_ORIGINS_VARIABLE] == PUBLIC_URL
