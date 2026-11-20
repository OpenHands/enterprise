"""Integration tests: real GithubIssue resolver conversation launch through the
real customer-gateway binder. Adapted from the GitLab scaffold at
tests/unit/integrations/gitlab/test_gitlab_resolver_gateway_integration.py.

Only true I/O leaves are stubbed: GithubServiceImpl, SaasSecretsStore,
CustomerGatewayStore's DB-touching staticmethods, sandbox/agent-server HTTP,
and resolve_org_for_repo. The real ResolverUserContext, GithubIssue, and
LiveStatusAppConversationService / bind_start_request path run unmocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from jinja2 import Environment
from pydantic import SecretStr

from integrations.github.github_view import GithubIssue
from integrations.types import UserData
from openhands.agent_server.models import StartConversationRequest
from openhands.app_server.app_conversation.live_status_app_conversation_service import (  # noqa: E501
    LiveStatusAppConversationService,
)
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    ExposedUrl,
    SandboxInfo,
    SandboxStatus,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.workspace import LocalWorkspace
from server.customer_gateway import service as gw_service
from server.customer_gateway.tokens import verify_inference_token

SHARED_ORG_ID = uuid4()
ACTOR_IDS = {'sue': str(uuid4()), 'dave': str(uuid4())}

RELAY = 'https://app.example/api/customer-gateway/v1'
ON_ENV = {
    'CUSTOMER_GATEWAY_ENABLED': 'true',
    'CUSTOMER_GATEWAY_RELAY_URL': RELAY,
    'CUSTOMER_GATEWAY_TOKEN_URL': 'https://app.example/api/customer-gateway/token',
}


def _real_start_request() -> StartConversationRequest:
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


def _real_sandbox(credential_org_id, created_by_user_id) -> SandboxInfo:
    return SandboxInfo(
        id='sb-github-1',
        created_by_user_id=created_by_user_id,
        credential_org_id=credential_org_id,
        sandbox_spec_id='spec',
        status=SandboxStatus.RUNNING,
        session_api_key='session-key',
        exposed_urls=[
            ExposedUrl(name=AGENT_SERVER, url='http://agent-server:8000', port=60000)
        ],
    )


def _build_saas_user_auth(actor_id: str) -> Mock:
    auth = Mock()
    auth.get_user_id = AsyncMock(return_value=actor_id)
    auth.get_user_email = AsyncMock(return_value=None)
    auth.get_user_settings = AsyncMock(return_value=None)
    auth.get_named_oauth_secrets = AsyncMock(return_value=None)
    return auth


def _build_real_service(
    sandbox: SandboxInfo, placeholder_user_id: str
) -> tuple[LiveStatusAppConversationService, Mock]:
    mock_user_context = Mock()
    mock_user_context.get_user_id = AsyncMock(return_value=placeholder_user_id)
    mock_user_context.get_user_email = AsyncMock(return_value=None)
    mock_user_context.get_named_oauth_secrets = AsyncMock(return_value=None)
    mock_user_context.get_user_info = AsyncMock(
        return_value=Mock(
            id=placeholder_user_id,
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
        return_value=_real_start_request()
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


def _get_service_for(svc, expected_resolver_org_id):
    import contextlib

    from integrations.resolver_context import ResolverUserContext
    from openhands.app_server.user.specifiy_user_context import USER_CONTEXT_ATTR

    @contextlib.asynccontextmanager
    async def _get_service(injector_state):
        ctx = getattr(injector_state, USER_CONTEXT_ATTR)
        assert isinstance(ctx, ResolverUserContext)
        assert ctx.resolver_org_id == expected_resolver_org_id
        svc.user_context = ctx
        yield svc

    return _get_service


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


def _github_issue(full_repo_name: str, keycloak_user_id: str) -> GithubIssue:
    return GithubIssue(
        issue_number=7,
        installation_id=123,
        full_repo_name=full_repo_name,
        is_public_repo=False,
        user_info=UserData(
            user_id=1, username='actor', keycloak_user_id=keycloak_user_id
        ),
        raw_payload=Mock(),
        conversation_id='conv',
        uuid=None,
        should_extract=False,
        send_summary_instruction=False,
        title='ignored',
        description='ignored',
        previous_comments=[],
    )


@pytest.fixture
def _no_external_github_io():
    """Stub the only real network/sandbox-IO leaves GithubIssue would
    otherwise make: GithubServiceImpl (real GitHub API) and SaasSecretsStore
    (secrets DB)."""
    with (
        patch(
            'integrations.github.github_view.GithubServiceImpl'
        ) as mock_gh_service_cls,
        patch(
            'integrations.github.github_view.SaasSecretsStore.get_instance',
            AsyncMock(return_value=None),
        ),
    ):
        inst = mock_gh_service_cls.return_value
        inst.get_issue_or_pr_comments = AsyncMock(return_value=[])
        inst.get_issue_or_pr_title_and_body = AsyncMock(
            return_value=('Real bug title', 'Real bug body')
        )
        yield


@pytest.fixture
def jinja_env() -> Environment:
    from pathlib import Path

    from jinja2 import FileSystemLoader

    repo_root = Path(__file__).resolve().parents[4]
    return Environment(
        loader=FileSystemLoader(
            str(
                repo_root
                / 'openhands/app_server/integrations/templates/resolver/github'
            )
        )
    )


def _gw_bound_patches(gw, policy_version=3):
    return [
        patch.object(
            gw_service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
        patch.object(
            gw_service.CustomerGatewayStore,
            'current_policy_version',
            AsyncMock(return_value=policy_version),
        ),
    ]


def _gw_denied_patches(gw, reason):
    return [
        patch.object(
            gw_service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
        patch.object(
            gw_service.CustomerGatewayStore,
            'current_policy_version',
            AsyncMock(side_effect=gw_service.CustomerGatewayDenied(reason)),
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('actor_name', ['sue', 'dave'])
async def test_mapped_member_issue_binds_through_real_gateway_distinct_claims(
    monkeypatch, jinja_env, _no_external_github_io, actor_name
):
    """Two different human-actor UUIDs in the SAME gateway-enabled org each
    get a real conversation launch bound to that org's relay/token through
    the unmocked bind_start_request path, using the real ResolverUserContext
    GithubIssue._create_v1_conversation builds (see _get_service_for). The
    per-actor JWT claim on the posted agent-server payload is asserted to
    match that specific actor's own UUID -- i.e. same org, distinct token
    claims per actor, not a single shared/cached token."""
    org_id = SHARED_ORG_ID
    actor = ACTOR_IDS[actor_name]
    assert len(set(ACTOR_IDS.values())) == 2
    gw = _gw(org_id)

    for k, v in ON_ENV.items():
        monkeypatch.setenv(k, v)

    jwt_service = gw_service._jwt()

    sandbox = _real_sandbox(org_id, actor)
    svc, mock_httpx_client = _build_real_service(sandbox, actor)
    issue = _github_issue('acme/widgets', keycloak_user_id=actor)
    saas_user_auth = _build_saas_user_auth(actor)
    _get_service = _get_service_for(svc, org_id)

    mock_conversation_info_class = Mock()
    mock_conversation_info_class.model_validate.side_effect = lambda data: Mock(
        id=data['id'], agent=Mock()
    )

    import contextlib

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch(
                'integrations.github.github_view.resolve_org_for_repo',
                AsyncMock(return_value=org_id),
            )
        )
        stack.enter_context(
            patch(
                'integrations.github.github_view.get_app_conversation_service',
                _get_service,
            )
        )
        stack.enter_context(
            patch(
                'openhands.app_server.app_conversation'
                '.live_status_app_conversation_service.ConversationInfo',
                mock_conversation_info_class,
            )
        )
        for p in _gw_bound_patches(gw):
            stack.enter_context(p)
        stack.enter_context(patch.object(gw_service, '_jwt', return_value=jwt_service))

        conversation_id = await issue.initialize_new_conversation()
        assert issue.resolved_org_id == org_id
        await issue._create_v1_conversation(
            jinja_env=jinja_env,
            saas_user_auth=saas_user_auth,
            conversation_id=conversation_id,
        )

    posted = mock_httpx_client.post.await_args_list[0]
    posted_llm = posted.kwargs['json']['agent']['llm']
    assert posted_llm['base_url'] == RELAY
    assert posted_llm['api_key'] != 'managed'
    posted_token = posted_llm['api_key']
    claims = verify_inference_token(jwt_service, posted_token)
    # Distinct-claims assertion: this actor's own UUID, not a shared value.
    assert (claims.user_id, claims.org_id, claims.session_id) == (
        actor,
        str(org_id),
        'sb-github-1',
    )


@pytest.mark.asyncio
async def test_denied_mapping_no_fallback_triggers_resolver(
    monkeypatch, jinja_env, _no_external_github_io
):
    """A CustomerGatewayDenied('not_a_member') from the store must propagate
    through mint_for_sandbox -> bind_start_request -> the real ERROR-status
    handling in GithubIssue._create_v1_conversation (unmodified production
    code) as a RuntimeError, with NO managed-key fallback and NO
    agent-server POST -- i.e. denied mapping triggers the resolver's error
    path rather than silently falling back to a default/managed key."""
    org_id = uuid4()
    actor = str(uuid4())
    gw = _gw(org_id)

    for k, v in ON_ENV.items():
        monkeypatch.setenv(k, v)

    sandbox = _real_sandbox(org_id, actor)
    svc, mock_httpx_client = _build_real_service(sandbox, actor)
    issue = _github_issue('acme/widgets', keycloak_user_id=actor)
    saas_user_auth = _build_saas_user_auth(actor)
    _get_service = _get_service_for(svc, org_id)

    import contextlib

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch(
                'integrations.github.github_view.resolve_org_for_repo',
                AsyncMock(return_value=org_id),
            )
        )
        stack.enter_context(
            patch(
                'integrations.github.github_view.get_app_conversation_service',
                _get_service,
            )
        )
        for p in _gw_denied_patches(gw, 'not_a_member'):
            stack.enter_context(p)

        conversation_id = await issue.initialize_new_conversation()
        assert issue.resolved_org_id == org_id
        with pytest.raises(RuntimeError) as exc_info:
            await issue._create_v1_conversation(
                jinja_env=jinja_env,
                saas_user_auth=saas_user_auth,
                conversation_id=conversation_id,
            )
    assert 'not_a_member' in str(exc_info.value)
    mock_httpx_client.post.assert_not_called()
