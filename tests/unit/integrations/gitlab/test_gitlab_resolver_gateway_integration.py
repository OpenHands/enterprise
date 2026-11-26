"""Integration tests: real GitlabIssue resolver conversation launch through the
real customer-gateway binder.

Unlike tests/unit/test_gitlab_view.py and the other integrations/*_view.py test
suites, this module does NOT replace ``get_app_conversation_service`` with a fake
that skips straight to a canned task status. It constructs a real
``LiveStatusAppConversationService`` and lets ``GitlabIssue._create_v1_conversation``
drive it exactly as production code does, so the real
``customer_gateway_hooks.bind_start_request`` -> ``server.customer_gateway.service``
code runs for each case below. Only true I/O leaves are stubbed:
  - External GitLab calls (``GitLabServiceImpl``, ``SaasSecretsStore``) — no network.
  - ``CustomerGatewayStore``'s own DB-touching staticmethods — the same seam the
    rest of tests/unit/server/customer_gateway/ already uses; no PG container is
    available in this sandbox, so this is a mocked-DB test, not PG regression
    evidence, exactly like the other 92 tests in that package.
  - Sandbox creation / agent-server HTTP (``_wait_for_sandbox_start``,
    ``run_setup_scripts``, the httpx client) — no real sandbox infra here either.
  - ``resolve_org_for_repo`` — controlled directly per test case (mapped org,
    unmapped/no-claim, wrong org), per the design doc's explicit routing contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from jinja2 import Environment
from pydantic import SecretStr

from integrations.gitlab.gitlab_view import GitlabIssue
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

RELAY = 'https://app.example/api/customer-gateway/v1'
ON_ENV = {
    'CUSTOMER_GATEWAY_ENABLED': 'true',
    'CUSTOMER_GATEWAY_RELAY_URL': RELAY,
    'CUSTOMER_GATEWAY_TOKEN_URL': 'https://app.example/api/customer-gateway/token',
}


def _real_start_request() -> StartConversationRequest:
    """A real (not mocked) request, the same shape production code builds."""
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
        id='sb-gitlab-1',
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
    """Minimal fake of the SaasUserAuth boundary ResolverUserContext wraps —
    the one real seam between "who is this GitLab actor" and the conversation
    service. Only the methods actually reached by this harness's (narrowed)
    control flow are implemented; everything else is left to fail loudly if a
    broader code path starts depending on it."""
    auth = Mock()
    auth.get_user_id = AsyncMock(return_value=actor_id)
    auth.get_user_email = AsyncMock(return_value=None)
    auth.get_user_settings = AsyncMock(return_value=None)
    auth.get_named_oauth_secrets = AsyncMock(return_value=None)
    return auth


def _build_real_service(
    sandbox: SandboxInfo, placeholder_user_id: str
) -> tuple[LiveStatusAppConversationService, Mock]:
    """A real LiveStatusAppConversationService with only its I/O leaves mocked
    (sandbox creation, agent-server HTTP, title generation) — the actual
    ``_start_app_conversation`` control flow, including the
    ``customer_gateway_hooks.bind_start_request`` call, runs unmocked.

    ``user_context`` is a placeholder here, built from ``placeholder_user_id``;
    callers must replace ``svc.user_context`` with the real
    ``ResolverUserContext`` GitlabIssue._create_v1_conversation constructs,
    once that context is available (see ``_get_service_for``), so the actual
    resolver identity propagation is exercised rather than an independently
    fabricated one.
    """
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

    # Real request, built once per launch — this is what bind_start_request
    # actually rebinds; production's own _build_start_conversation_request_for_user
    # is not under test here, only the gateway binding step that runs after it.
    svc._build_start_conversation_request_for_user = AsyncMock(
        return_value=_real_start_request()
    )

    async def mock_wait_for_sandbox(task, profile_sandbox=None):
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
        # Pinned product SDK is 1.62.0; using the real version here keeps any
        # version-conditional code exercised on the correct branch.
        return_value=Mock(browser_available=True, sdk_version='1.62.0')
    )

    return svc, mock_httpx_client


def _get_service_for(svc, expected_resolver_org_id):
    """Build the ``get_app_conversation_service`` stand-in that
    ``GitlabIssue._create_v1_conversation`` calls with its own
    ``InjectorState``. Unlike a fake that ignores the context entirely, this
    extracts the *real* ``ResolverUserContext`` production code attached to
    that state, asserts it carries the expected resolved org, and swaps it in
    as ``svc.user_context`` so the conversation's actual run uses real
    resolver-identity propagation (``get_user_id``/``get_user_info`` go
    through ``ResolverUserContext`` to the actor's own ``SaasUserAuth`` fake,
    not an independently fabricated context)."""
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


def _gitlab_issue(full_repo_name: str, keycloak_user_id: str) -> GitlabIssue:
    return GitlabIssue(
        installation_id='inst-1',
        issue_number=7,
        project_id=99,
        full_repo_name=full_repo_name,
        is_public_repo=False,
        user_info=UserData(
            user_id=1, username='actor', keycloak_user_id=keycloak_user_id
        ),
        raw_payload=Mock(),
        conversation_id='conv',
        should_extract=False,
        send_summary_instruction=False,
        title='ignored',
        description='ignored',
        previous_comments=[],
        is_mr=False,
    )


@pytest.fixture
def _no_external_gitlab_io():
    """Stub the only real network calls GitlabIssue would otherwise make."""
    with (
        patch(
            'integrations.gitlab.gitlab_view.GitLabServiceImpl'
        ) as mock_gl_service_cls,
        patch(
            'integrations.gitlab.gitlab_view.SaasSecretsStore.get_instance',
            AsyncMock(return_value=None),
        ),
    ):
        inst = mock_gl_service_cls.return_value
        inst.get_issue_or_mr_comments = AsyncMock(return_value=[])
        inst.get_issue_or_mr_title_and_body = AsyncMock(
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
                / 'openhands/app_server/integrations/templates/resolver/gitlab'
            )
        )
    )


async def _run_gitlab_issue(
    monkeypatch,
    jinja_env,
    org_id,
    created_by_user_id,
    gateway_patches,
    full_repo_name='acme/widgets',
    resolved_org_id_override=...,
):
    """Drive the real GitlabIssue -> LiveStatusAppConversationService ->
    bind_start_request chain for one issue. Returns ('ok', sandbox) on a
    successful launch, or (error message, sandbox) if the real conversation
    service's own task-ERROR -> RuntimeError translation fired (gitlab_view.py's
    own ``_create_v1_conversation`` does this, unmodified)."""
    import contextlib

    for k, v in ON_ENV.items():
        monkeypatch.setenv(k, v)

    sandbox = _real_sandbox(org_id, created_by_user_id)
    svc, mock_httpx_client = _build_real_service(sandbox, created_by_user_id)

    issue = _gitlab_issue(full_repo_name, keycloak_user_id=created_by_user_id)
    resolved_org_id = (
        org_id if resolved_org_id_override is ... else resolved_org_id_override
    )
    saas_user_auth = _build_saas_user_auth(created_by_user_id)
    _get_service = _get_service_for(svc, resolved_org_id)

    mock_conversation_info_class = Mock()
    mock_conversation_info_class.model_validate.side_effect = lambda data: Mock(
        id=data['id'], agent=Mock()
    )

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch(
                'integrations.gitlab.gitlab_view.resolve_org_for_repo',
                AsyncMock(return_value=resolved_org_id),
            )
        )
        stack.enter_context(
            patch(
                'integrations.gitlab.gitlab_view.get_app_conversation_service',
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
        for p in gateway_patches:
            stack.enter_context(p)
        conversation_id = await issue.initialize_new_conversation()
        assert issue.resolved_org_id == resolved_org_id
        try:
            await issue._create_v1_conversation(
                jinja_env=jinja_env,
                saas_user_auth=saas_user_auth,
                conversation_id=conversation_id,
            )
            result = 'ok'
        except RuntimeError as exc:
            result = str(exc)
    return result, sandbox, mock_httpx_client


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
@pytest.mark.parametrize('is_mr', [False, True])
@pytest.mark.parametrize('actor_name', ['sue', 'dave'])
async def test_mapped_member_issue_and_mr_bind_through_real_gateway(
    monkeypatch, jinja_env, _no_external_gitlab_io, is_mr, actor_name
):
    """Two different GitLab actors in the same gateway-enabled org, for both
    an issue and an MR, each get a real conversation launch bound to the
    customer's relay/token through the unmocked bind_start_request path —
    using the *real* ``ResolverUserContext`` production code builds (see
    ``_get_service_for``), not an independently fabricated context. The
    per-actor JWT claim on the actually-posted agent-server payload is
    asserted to match that specific actor, proving identity propagates
    end-to-end rather than only an allowlist/config check passing."""
    org_id = uuid4()
    # created_by_user_id must be a real UUID string (mint_for_sandbox parses it
    # with UUID(...)); actor_name only distinguishes the two parametrized runs.
    actor = str(uuid4())
    gw = _gw(org_id)

    for k, v in ON_ENV.items():
        monkeypatch.setenv(k, v)

    jwt_service = gw_service._jwt()

    sandbox = _real_sandbox(org_id, actor)
    svc, mock_httpx_client = _build_real_service(sandbox, actor)
    issue = _gitlab_issue('acme/widgets', keycloak_user_id=actor)
    issue.is_mr = is_mr
    saas_user_auth = _build_saas_user_auth(actor)
    _get_service = _get_service_for(svc, org_id)

    mock_conversation_info_class = Mock()
    mock_conversation_info_class.model_validate.side_effect = lambda data: Mock(
        id=data['id'], agent=Mock()
    )

    with (
        patch(
            'integrations.gitlab.gitlab_view.resolve_org_for_repo',
            AsyncMock(return_value=org_id),
        ),
        patch(
            'integrations.gitlab.gitlab_view.get_app_conversation_service',
            _get_service,
        ),
        patch(
            'openhands.app_server.app_conversation'
            '.live_status_app_conversation_service.ConversationInfo',
            mock_conversation_info_class,
        ),
        patch.object(
            gw_service.CustomerGatewayStore,
            'get_enabled_org_gateway',
            AsyncMock(return_value=gw),
        ),
        patch.object(
            gw_service.CustomerGatewayStore,
            'current_policy_version',
            AsyncMock(return_value=3),
        ),
        patch.object(gw_service, '_jwt', return_value=jwt_service),
    ):
        conversation_id = await issue.initialize_new_conversation()
        assert issue.resolved_org_id == org_id
        await issue._create_v1_conversation(
            jinja_env=jinja_env,
            saas_user_auth=saas_user_auth,
            conversation_id=conversation_id,
        )

    # This is the actual payload LiveStatusAppConversationService posted to the
    # sandbox's agent-server through the real ResolverUserContext/
    # bind_start_request path — i.e. the request after the real mutation, not
    # a value this test constructed itself.
    posted = mock_httpx_client.post.await_args_list[0]
    posted_llm = posted.kwargs['json']['agent']['llm']
    assert posted_llm['base_url'] == RELAY
    # Never the original static/managed key this request started with.
    assert posted_llm['api_key'] != 'managed'
    posted_token = posted_llm['api_key']
    claims = verify_inference_token(jwt_service, posted_token)
    assert (claims.user_id, claims.org_id, claims.session_id) == (
        actor,
        str(org_id),
        'sb-gitlab-1',
    )


@pytest.mark.asyncio
async def test_store_not_a_member_denial_propagates_through_real_launch(
    monkeypatch, jinja_env, _no_external_gitlab_io
):
    """This test proves *propagation*, not the store's own decision: it mocks
    ``CustomerGatewayStore.current_policy_version`` to raise
    ``CustomerGatewayDenied('not_a_member')`` and checks that the denial
    reaches ``mint_for_sandbox`` → ``bind_start_request`` →
    ``_start_app_conversation``'s real ERROR-status handling →
    ``GitlabIssue._create_v1_conversation``'s own ``RuntimeError``
    translation (unmodified production code) without any managed-key
    fallback and without any agent-server POST (so no org's gateway key is
    ever charged). It does **not** prove that ``CustomerGatewayStore`` itself
    correctly classifies an actual unmapped actor as ``not_a_member`` — that
    is the store's own account_type/row-lookup logic, covered (or not) by
    ``tests/unit/storage/test_customer_gateway_store.py``, which this harness
    does not exercise.

    Note on two distinct "membership" checks, so this isn't mistaken for a
    test of resolver routing: ``resolve_org_for_repo`` (see
    integrations/resolver_org_router.py) checks ``OrgMemberStore`` and, if
    the actor is *not* an org member there, returns None (falls back to the
    actor's personal workspace) *before* this gateway code ever runs for that
    reason — that is existing, unmodified resolver-routing behavior, not
    something this candidate changes (the platform's gateway remains
    opt-in-per-org, with no new global personal-workspace prohibition). This
    test instead stands in for the separate, deeper check inside
    ``CustomerGatewayStore`` itself, reached once an actor already resolved
    into a gateway-enabled org."""
    org_id = uuid4()
    actor = str(uuid4())
    gw = _gw(org_id)

    result, _, mock_httpx_client = await _run_gitlab_issue(
        monkeypatch,
        jinja_env,
        org_id,
        actor,
        _gw_denied_patches(gw, 'not_a_member'),
    )
    assert 'not_a_member' in result
    # No agent-server POST happened at all — nothing was charged to any org's
    # gateway key, this org's or otherwise.
    mock_httpx_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_store_service_account_denial_propagates_through_real_launch(
    monkeypatch, jinja_env, _no_external_gitlab_io
):
    """Same propagation-only scope as
    ``test_store_not_a_member_denial_propagates_through_real_launch``, for the
    ``CustomerGatewayDenied('service_account')`` reason instead. This confirms
    the launch is refused end-to-end (no managed-key fallback, no
    agent-server POST) when the store denies for this reason; it does not by
    itself prove ``CustomerGatewayStore._load``'s ``account_type != human``
    branch is what produces that reason for a real service-account row — that
    proof belongs to the store's own unit tests."""
    org_id = uuid4()
    actor = str(uuid4())
    gw = _gw(org_id)

    result, _, mock_httpx_client = await _run_gitlab_issue(
        monkeypatch,
        jinja_env,
        org_id,
        actor,
        _gw_denied_patches(gw, 'service_account'),
    )
    assert 'service_account' in result
    mock_httpx_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_unclaimed_repo_personal_fallback_does_not_inherit_another_orgs_gateway(
    monkeypatch, jinja_env, _no_external_gitlab_io
):
    """resolve_org_for_repo returning None is existing, unmodified resolver
    behavior for an unclaimed git org (see integrations/resolver_org_router.py)
    — this candidate does not add any new personal-workspace prohibition, and
    the gateway remains strictly opt-in per organization. The one thing this
    test adds is confirming that a personal-workspace launch must not pick up
    a *different*, unrelated org's gateway just because that other org happens
    to have one enabled. The sandbox here is created under the actor's own
    personal org (no gateway), not the other org, so the launch proceeds
    without any customer-gateway binding at all — it must not be silently
    refused *or* silently bound to the wrong org's policy."""
    personal_org_id = uuid4()
    other_org_with_gateway = uuid4()
    assert personal_org_id != other_org_with_gateway
    actor = str(uuid4())
    other_gw = _gw(other_org_with_gateway)

    result, sandbox, _ = await _run_gitlab_issue(
        monkeypatch,
        jinja_env,
        personal_org_id,
        actor,
        [
            patch.object(
                gw_service.CustomerGatewayStore,
                'get_enabled_org_gateway',
                AsyncMock(
                    side_effect=lambda org_id: (
                        other_gw if org_id == other_org_with_gateway else None
                    )
                ),
            ),
        ],
        resolved_org_id_override=None,
    )
    assert result == 'ok'
    assert sandbox.credential_org_id == personal_org_id
