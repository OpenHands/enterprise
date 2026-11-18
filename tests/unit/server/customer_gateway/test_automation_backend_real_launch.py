"""Drives the REAL automation-backend method -- not a hand-built request.

``openhands.automation.backends.cloud.CloudSandboxBackend.start_conversation``
(imported from the frozen reference tree at
/workspace/gateway-background-20261115/automation-reference, verified against
the actual deployed automation image: openhands-automation==1.32.1,
openhands-sdk==1.62.0, openhands-workspace==1.62.0, matching this Enterprise
checkout's pinned SDK) is called directly. It builds its own real JSON POST
body and sends it over a real httpx.AsyncClient; an httpx.MockTransport
parses that real body into a real AppConversationStartRequest and drains the
real LiveStatusAppConversationService.start_app_conversation() ->
customer_gateway_hooks.bind_start_request -> server.customer_gateway.service
binder -- the identical service fixture
test_automation_gateway_integration.py's own tests build and assert against.

Labeled scope (bounded per root review, see tests/unit/server/customer_gateway/
_backend_adapter.py's own docstring):
  - Real: CloudSandboxBackend.start_conversation's request construction,
    its httpx POST/GET calls (actual serialization boundary), the real
    service, and the real gateway binder/token-minting path.
  - Mocked I/O: the HTTP transport itself (httpx.MockTransport, standing in
    for the network), the automation-service's own API-key minting
    (``backend._ensure_api_key``, a Cloud credential unrelated to the
    gateway's owner-identity binding), and the GET conversation read-back
    (returns only the fields the backend itself reads).
  - The sandbox agent-server POST response (``mock_httpx_client.post``,
    already stubbed by ``_build_real_service``) is upgraded, for the
    positive case below, from the bare ``{"id": ...}`` fixture
    test_automation_gateway_integration.py's own tests use (sufficient for
    their call-shape/denial assertions) to one that echoes back the real
    ``agent``/``workspace`` fields from the actual posted request body --
    see ``_backend_adapter.make_sandbox_agent_server_echo_response`` and
    live_status_app_conversation_service.py's own comment that "the
    response echoes the same agent back through the AgentBase
    discriminator". With that, ``ConversationInfo.model_validate`` succeeds,
    the task reaches READY, and ``CloudSandboxBackend.start_conversation``
    actually returns a ``StartedConversation`` -- no expected exception on
    the positive path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from openhands.app_server.sandbox.remote_sandbox_service import (
    RemoteSandboxServiceInjector,
)
from server.customer_gateway import service as gw_service

from . import _backend_adapter as ba
from .test_automation_gateway_integration import (
    JWT,
    ON_ENV,
    SYNTHETIC_ORG_ID,
    _build_real_service,
    _gw,
    _sandbox,
)


@pytest.fixture
def remote_runtime():
    from types import SimpleNamespace

    injector = RemoteSandboxServiceInjector(
        api_url='http://runtime-api', api_key='test'
    )
    with patch.object(
        gw_service, 'get_global_config', return_value=SimpleNamespace(sandbox=injector)
    ):
        yield


@pytest.mark.usefixtures('remote_runtime')
async def test_real_backend_launch_posts_owner_scoped_token_to_sandbox(monkeypatch):
    """The actual CloudSandboxBackend.start_conversation call, driven end to
    end through the real service and real gateway binder, actually
    RETURNS a StartedConversation (task reaches READY) -- not an expected
    failure. Asserts the real sandbox agent-server POST (the one the
    gateway binder causes) carries a token scoped to the real owner -- the
    security-relevant assertion -- on the actual successful path."""
    for k, v in ON_ENV.items():
        monkeypatch.setenv(k, v)

    owner = str(uuid4())
    sandbox = _sandbox(owner, SYNTHETIC_ORG_ID)
    svc, mock_httpx_client = _build_real_service(sandbox, owner)
    ba.make_sandbox_agent_server_echo_response(mock_httpx_client)
    gw = _gw(SYNTHETIC_ORG_ID)

    conv_get = {
        'id': str(uuid4()),
        'created_by_user_id': owner,
        'sandbox_id': sandbox.id,
    }
    transport = ba.make_transport(svc, conv_get)
    run = ba.make_automation_run(owner, SYNTHETIC_ORG_ID, sandbox.id)
    backend = ba.make_cloud_backend('http://test', run)

    import httpx

    from openhands.automation.backends.base import StartedConversation

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
        async with httpx.AsyncClient(
            transport=transport, base_url='http://test'
        ) as client:
            ctx = ba.make_execution_context(sandbox.id, 'http://test', 'fake-key')
            start = ba.make_conversation_start()
            started = await backend.start_conversation(client, ctx, start)

    assert isinstance(started, StartedConversation)
    assert mock_httpx_client.post.await_count == 1
    posted_body = mock_httpx_client.post.await_args.kwargs.get('json')
    token = posted_body['agent']['llm']['api_key']
    from server.customer_gateway.tokens import verify_inference_token

    claims = verify_inference_token(JWT, token)
    assert claims.user_id == owner
    assert claims.org_id == str(SYNTHETIC_ORG_ID)


@pytest.mark.usefixtures('remote_runtime')
async def test_real_backend_launch_two_owners_same_org_get_distinct_tokens(
    monkeypatch,
):
    """Two distinct human owners in the SAME synthetic org, both driven
    through the actual successful (READY) launch path, must each receive a
    sandbox LLM token scoped to their own user_id -- never a shared key."""
    for k, v in ON_ENV.items():
        monkeypatch.setenv(k, v)

    import httpx

    from openhands.automation.backends.base import StartedConversation

    gw = _gw(SYNTHETIC_ORG_ID)
    tokens: dict[str, str] = {}
    for owner in (str(uuid4()), str(uuid4())):
        sandbox = _sandbox(owner, SYNTHETIC_ORG_ID)
        svc, mock_httpx_client = _build_real_service(sandbox, owner)
        ba.make_sandbox_agent_server_echo_response(mock_httpx_client)
        conv_get = {
            'id': str(uuid4()),
            'created_by_user_id': owner,
            'sandbox_id': sandbox.id,
        }
        transport = ba.make_transport(svc, conv_get)
        run = ba.make_automation_run(owner, SYNTHETIC_ORG_ID, sandbox.id)
        backend = ba.make_cloud_backend('http://test', run)

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
            async with httpx.AsyncClient(
                transport=transport, base_url='http://test'
            ) as client:
                ctx = ba.make_execution_context(sandbox.id, 'http://test', 'fake-key')
                start = ba.make_conversation_start()
                started = await backend.start_conversation(client, ctx, start)

        assert isinstance(started, StartedConversation)
        posted_body = mock_httpx_client.post.await_args.kwargs.get('json')
        tokens[owner] = posted_body['agent']['llm']['api_key']

    from server.customer_gateway.tokens import verify_inference_token

    owners = list(tokens)
    assert tokens[owners[0]] != tokens[owners[1]]
    for owner, token in tokens.items():
        claims = verify_inference_token(JWT, token)
        assert claims.user_id == owner
        assert claims.org_id == str(SYNTHETIC_ORG_ID)


@pytest.mark.usefixtures('remote_runtime')
@pytest.mark.parametrize(
    'denial_reason',
    ['not_a_member', 'service_account', 'revoked', 'disabled_membership'],
)
async def test_real_backend_launch_denied_never_posts_to_sandbox(
    monkeypatch, denial_reason
):
    """The real CloudSandboxBackend.start_conversation call must surface a
    real gateway denial as a failure, and the sandbox must never receive a
    POST at all (no fallback/shared key, no silent success)."""
    for k, v in ON_ENV.items():
        monkeypatch.setenv(k, v)

    owner = str(uuid4())
    sandbox = _sandbox(owner, SYNTHETIC_ORG_ID)
    svc, mock_httpx_client = _build_real_service(sandbox, owner)
    gw = _gw(SYNTHETIC_ORG_ID)

    conv_get = {
        'id': str(uuid4()),
        'created_by_user_id': owner,
        'sandbox_id': sandbox.id,
    }
    transport = ba.make_transport(svc, conv_get)
    run = ba.make_automation_run(owner, SYNTHETIC_ORG_ID, sandbox.id)
    backend = ba.make_cloud_backend('http://test', run)

    import httpx

    from openhands.automation.exceptions import ConversationStartError

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
        async with httpx.AsyncClient(
            transport=transport, base_url='http://test'
        ) as client:
            ctx = ba.make_execution_context(sandbox.id, 'http://test', 'fake-key')
            start = ba.make_conversation_start()
            with pytest.raises(ConversationStartError) as excinfo:
                await backend.start_conversation(client, ctx, start)

    assert denial_reason in str(excinfo.value)
    assert mock_httpx_client.post.await_count == 0
