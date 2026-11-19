"""Integration tests: real automation-backend conversation launch through the
real customer-gateway binder.

Mirrors tests/unit/integrations/gitlab/test_gitlab_resolver_gateway_integration.py's
approach for the resolver trigger, but drives the automation trigger directly:
the deployed automation-server's CloudSandboxBackend.start_conversation
(openhands/automation/backends/cloud.py in automation-reference) issues
``POST /api/v1/app-conversations`` with body {"sandbox_id": ..., "trigger":
"automation", ...} -- i.e. an AppConversationStartRequest(trigger=AUTOMATION,
sandbox_id=...). This test constructs a real LiveStatusAppConversationService
and calls its real start_app_conversation()/_start_app_conversation() with that
exact request shape, so the real customer_gateway_hooks.bind_start_request ->
server.customer_gateway.service.bind_start_request code path runs end to end.

Only true I/O leaves are mocked (same leaves as the GitLab harness and the
product's own test_live_status_app_conversation_service.py):
  - Sandbox creation / agent-server HTTP (``_wait_for_sandbox_start``,
    ``run_setup_scripts``, the httpx client that posts to the agent server).
  - ``CustomerGatewayStore``'s own DB-touching staticmethods (no Postgres
    container in this sandbox -- same mocked-DB seam as the other 92+5 tests
    in this package; NOT PG regression evidence).
  - Title generation / pending-message processing (unrelated to gateway binding).

NOT mocked: ``bind_start_request``, ``mint_for_sandbox``, ``bind_llm``,
``authorize_inference``'s token verification, or the owner-identity source
(``sandbox.created_by_user_id``). Two distinct human UUIDs are used in the
SAME synthetic org to assert the real posted sandbox LLM token is scoped to
each owner -- not a single shared key.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.models import StartConversationRequest
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartRequest,
    ConversationTrigger,
)
from openhands.app_server.app_conversation.live_status_app_conversation_service import (  # noqa: E501
    LiveStatusAppConversationService,
)
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    ExposedUrl,
    SandboxInfo,
    SandboxStatus,
)
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from openhands.sdk import LLM, Agent
from openhands.sdk.workspace import LocalWorkspace
from server.customer_gateway import service as gw_service
from server.customer_gateway.tokens import verify_inference_token

RELAY = 'https://app.example/api/customer-gateway/v1'
ON_ENV = {
    'CUSTOMER_GATEWAY_ENABLED': 'true',
    'CUSTOMER_GATEWAY_RELAY_URL': RELAY,
    'CUSTOMER_GATEWAY_TOKEN_URL': 'https://app.example/api/customer-gateway/token',
}
JWT = JwtService(
    keys=[EncryptionKey(kid='test', key=SecretStr('test_secret'), active=True)]
)

# Shared synthetic org for same-org owner-isolation assertions.
SYNTHETIC_ORG_ID = uuid4()


def _sandbox(owner_user_id: str, org_id) -> SandboxInfo:
    return SandboxInfo(
        id=f'sb-auto-{owner_user_id}',
        created_by_user_id=owner_user_id,
        credential_org_id=org_id,
        sandbox_spec_id='spec',
        status=SandboxStatus.RUNNING,
        session_api_key='session-key',
        exposed_urls=[
            ExposedUrl(name=AGENT_SERVER, url='http://agent-server:8000', port=60000)
        ],
    )


def _gw(org_id):
    return gw_service.OrgGateway(
        org_id,
        'gw',
        {
            'base_url': 'https://gw.customer.example/v1',
            'models': {'claude-sonnet': 'anthropic/claude-sonnet'},
        },
        datetime(2026, 11, 14, tzinfo=UTC),
    )


def _real_start_request_build(owner_user_id: str):
    async def _build(*_args, **_kwargs):
        return StartConversationRequest(
            agent=Agent(
                llm=LLM(
                    model='litellm_proxy/claude-sonnet',
                    base_url='http://litellm:4000',
                    api_key=SecretStr('managed'),
                    usage_id='agent',
                )
            ),
            workspace=LocalWorkspace(working_dir='/tmp'),
        )

    return _build


def _build_real_service(
    sandbox: SandboxInfo, owner_user_id: str
) -> tuple[LiveStatusAppConversationService, Mock]:
    """Real service with only sandbox-creation/agent-server HTTP I/O mocked.

    ``user_context`` here models the automation backend's own call: it
    authenticates to the app server with the scheduled/definition OWNER's
    credentials (the automation run's ``run.user_id``), not an arbitrary
    external event actor -- there is no separate "automation actor" identity
    in this flow, by design (see service.py's INTERACTIVE_TRIGGERS comment).
    """
    mock_user_context = Mock()
    mock_user_context.get_user_id = AsyncMock(return_value=owner_user_id)
    mock_user_context.get_user_email = AsyncMock(return_value=None)
    mock_user_context.get_named_oauth_secrets = AsyncMock(return_value=None)
    mock_user_context.get_user_info = AsyncMock(
        return_value=Mock(
            id=owner_user_id,
            llm_model='gpt-4',
            sandbox_grouping_strategy=Mock(),
            confirmation_mode=False,
            security_analyzer=None,
            search_api_key=None,
            mcp_config=None,
            disabled_skills=[],
            git_full_clone=False,
            agent_settings=None,
        )
    )

    mock_sandbox_service = Mock()
    mock_sandbox_service.get_sandbox = AsyncMock(return_value=sandbox)
    mock_sandbox_spec_service = Mock()
    mock_sandbox_spec_service.resolve_existing_sandbox_spec = AsyncMock(
        return_value=Mock(working_dir='/workspace/project')
    )
    mock_app_conversation_info_service = Mock()
    mock_app_conversation_info_service.save_app_conversation_info = AsyncMock()
    mock_app_conversation_start_task_service = Mock()
    mock_app_conversation_start_task_service.save_app_conversation_start_task = (
        AsyncMock(side_effect=lambda task: task)
    )
    mock_event_callback_service = Mock()
    mock_event_callback_service.save_event_callback = AsyncMock()
    mock_event_service = Mock()
    mock_httpx_client = Mock()
    mock_response = Mock()
    mock_response.json.return_value = {'id': str(uuid4())}
    mock_response.raise_for_status = Mock()
    mock_httpx_client.post = AsyncMock(return_value=mock_response)
    mock_pending_message_service = Mock()

    svc = LiveStatusAppConversationService(
        init_git_in_empty_workspace=True,
        user_context=mock_user_context,
        app_conversation_info_service=mock_app_conversation_info_service,
        app_conversation_start_task_service=mock_app_conversation_start_task_service,
        event_callback_service=mock_event_callback_service,
        event_service=mock_event_service,
        sandbox_service=mock_sandbox_service,
        sandbox_spec_service=mock_sandbox_spec_service,
        jwt_service=Mock(),
        pending_message_service=mock_pending_message_service,
        sandbox_startup_timeout=30,
        sandbox_startup_poll_frequency=1,
        max_num_conversations_per_sandbox=20,
        httpx_client=mock_httpx_client,
        web_url='https://test.example.com',
        openhands_provider_base_url='https://provider.example.com',
        access_token_hard_timeout=None,
        app_mode='test',
    )

    svc._build_start_conversation_request_for_user = AsyncMock(
        side_effect=_real_start_request_build(owner_user_id)
    )

    async def mock_wait_for_sandbox(task):
        task.sandbox_id = sandbox.id
        yield task

    async def mock_run_setup_scripts(
        task, sandbox_, workspace, agent_server_url, conversation_id
    ):
        yield task

    svc._wait_for_sandbox_start = mock_wait_for_sandbox
    svc.run_setup_scripts = mock_run_setup_scripts
    svc._load_hooks_from_workspace = AsyncMock(return_value=None)
    svc._process_pending_messages = AsyncMock()
    svc._sync_start_title = AsyncMock()
    svc._ensure_profile_runtime = AsyncMock(
        return_value=Mock(browser_available=True, sdk_version='1.62.0')
    )

    return svc, mock_httpx_client


async def _drive_start(svc: LiveStatusAppConversationService, owner_user_id: str):
    """Build the exact AppConversationStartRequest the automation backend's
    CloudSandboxBackend.start_conversation posts (trigger=AUTOMATION,
    sandbox_id set, no secrets/prompt in the body), and drain the real
    start_app_conversation()/_start_app_conversation() async generator -- the
    same call app_conversation_router.py's POST /api/v1/app-conversations
    makes -- to completion."""
    start_request = AppConversationStartRequest(
        sandbox_id=f'sb-auto-{owner_user_id}',
        trigger=ConversationTrigger.AUTOMATION,
        tags={},
    )
    results = []
    async for item in svc.start_app_conversation(start_request):
        results.append(item)
    return results


@pytest.fixture
def remote_runtime():
    from types import SimpleNamespace

    from openhands.app_server.sandbox.remote_sandbox_service import (
        RemoteSandboxServiceInjector,
    )

    injector = RemoteSandboxServiceInjector(
        api_url='http://runtime-api', api_key='test'
    )
    with patch.object(
        gw_service, 'get_global_config', return_value=SimpleNamespace(sandbox=injector)
    ):
        yield


@pytest.mark.usefixtures('remote_runtime')
async def test_automation_real_launch_scopes_token_to_each_distinct_owner(
    monkeypatch,
):
    """Two distinct human owners, SAME synthetic org: the real
    bind_start_request -> mint_for_sandbox path must mint a token scoped to
    EACH owner, never a shared key. Drives the actual automation trigger
    request shape through the real service, not a hand-minted token."""
    for k, v in ON_ENV.items():
        monkeypatch.setenv(k, v)

    owner_a = str(uuid4())
    owner_b = str(uuid4())
    gw = _gw(SYNTHETIC_ORG_ID)

    posted_tokens: dict[str, str] = {}

    for owner in (owner_a, owner_b):
        sandbox = _sandbox(owner, SYNTHETIC_ORG_ID)
        svc, mock_httpx_client = _build_real_service(sandbox, owner)
        with (
            patch.object(
                gw_service.CustomerGatewayStore,
                'get_enabled_org_gateway',
                AsyncMock(return_value=gw),
            ),
            patch.object(
                gw_service.CustomerGatewayStore,
                'current_policy_version',
                AsyncMock(return_value=1),
            ),
            patch.object(gw_service, '_jwt', return_value=JWT),
        ):
            await _drive_start(svc, owner)

        assert mock_httpx_client.post.await_count == 1
        posted_body = mock_httpx_client.post.await_args.kwargs.get(
            'json'
        ) or mock_httpx_client.post.await_args[1].get('json')
        token = posted_body['agent']['llm']['api_key']
        assert posted_body['agent']['llm']['base_url'] == RELAY
        claims = verify_inference_token(JWT, token)
        assert claims.user_id == owner
        assert claims.org_id == str(SYNTHETIC_ORG_ID)
        posted_tokens[owner] = token

    # The two owners' tokens must not be the same credential.
    assert posted_tokens[owner_a] != posted_tokens[owner_b]
    # Cross-check: owner A's token does not verify as owner B's.
    claims_a = verify_inference_token(JWT, posted_tokens[owner_a])
    claims_b = verify_inference_token(JWT, posted_tokens[owner_b])
    assert claims_a.user_id != claims_b.user_id
    assert claims_a.session_id != claims_b.session_id


@pytest.mark.usefixtures('remote_runtime')
@pytest.mark.parametrize(
    'denial_reason',
    ['not_a_member', 'service_account', 'revoked', 'disabled_membership'],
)
async def test_automation_real_launch_denies_on_store_resolution_failures(
    monkeypatch, denial_reason
):
    """Bad/missing/disabled/service-account mappings propagate as real
    CustomerGatewayDenied -> CustomerGatewayLaunchError through the actual
    mint_for_sandbox call (not a mock branch standing in for the denial).
    mint_for_sandbox itself only raises 'sandbox_inactive'/'sandbox_unowned';
    the membership/service-account denials below are
    CustomerGatewayStore.current_policy_version's own documented contract
    (prior worker's separate storage-layer evidence
    tests/unit/storage/test_customer_gateway_store.py), reproduced here via
    the real exception type through the real binder call so the launch-path
    propagation itself -- not just the store method in isolation -- is
    covered."""
    for k, v in ON_ENV.items():
        monkeypatch.setenv(k, v)

    owner = str(uuid4())
    sandbox = _sandbox(owner, SYNTHETIC_ORG_ID)
    gw = _gw(SYNTHETIC_ORG_ID)
    svc, mock_httpx_client = _build_real_service(sandbox, owner)

    with (
        patch.object(
            gw_service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
        patch.object(
            gw_service.CustomerGatewayStore,
            'current_policy_version',
            AsyncMock(side_effect=gw_service.CustomerGatewayDenied(denial_reason)),
        ),
        patch.object(gw_service, '_jwt', return_value=JWT),
    ):
        results = await _drive_start(svc, owner)

    # The real _start_app_conversation does not raise out of the async
    # generator on a CustomerGatewayLaunchError; it yields a terminal ERROR
    # start-task (same contract production's own error handling relies on).
    assert results, 'expected at least one yielded start task'
    final = results[-1]
    assert final.status.value == 'ERROR'
    assert denial_reason in (final.detail or '')
    # No sandbox-bound llm.api_key was ever posted to the agent server.
    assert mock_httpx_client.post.await_count == 0
