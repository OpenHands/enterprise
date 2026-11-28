"""Launch entry point: an approved gateway default alias for a member with no model.

Drives the real ``_start_app_conversation`` with the real ``CustomerGatewayStore`` on
PostgreSQL, the real selection hook and the real binder. The start-request builder is
reduced to the normal LLM path (``_configure_llm`` and ``create_agent``), so the
condenser is the one the SDK derives from the agent LLM.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from openhands.agent_server.models import (
    SendMessageRequest,
    StartConversationRequest,
    TextContent,
)
from openhands.app_server.app_conversation import (
    live_status_app_conversation_service as live_status,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartRequest,
)
from openhands.app_server.user.user_models import UserInfo
from openhands.app_server.utils import customer_gateway_hooks
from openhands.sdk.settings import default_agent_settings
from openhands.sdk.workspace import LocalWorkspace
from server import constants
from server.customer_gateway import service as gw_service
from server.customer_gateway.tokens import verify_inference_token
from storage.customer_gateway_store import CustomerGatewayStore
from storage.org_member import OrgMember
from storage.role import Role

from .test_automation_gateway_integration import (
    JWT,
    ON_ENV,
    RELAY,
    _build_real_service,
    _sandbox,
)

ORG_ID = UUID('e1111111-1111-1111-1111-111111111111')
FIRST = UUID('e2222222-2222-2222-2222-222222222222')
CATALOG = {'sonnet': 'anthropic/claude-sonnet', 'deepseek': 'deepseek/chat'}


@pytest.fixture
def gateway(monkeypatch, async_session_maker, session_maker, create_org, create_user):
    from types import SimpleNamespace

    from openhands.app_server.sandbox.remote_sandbox_service import (
        RemoteSandboxServiceInjector,
    )

    for k, v in ON_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(constants, 'DEPLOYMENT_MODE', 'self_hosted')
    monkeypatch.setattr(constants, 'ENABLE_LEGACY_LITELLM', True)
    monkeypatch.setattr(constants, 'LITELLM_DEFAULT_MODEL', None)
    monkeypatch.setattr(constants, 'OPENHANDS_LLM_PROVIDER_ROUTE', None)
    create_org(id=ORG_ID)
    with session_maker() as session:
        role = Role(name=f'member-{uuid4().hex[:8]}', rank=3)
        session.add(role)
        session.flush()
        create_user(id=FIRST, current_org_id=ORG_ID)
        session.add(
            OrgMember(
                org_id=ORG_ID,
                user_id=FIRST,
                role_id=role.id,
                llm_api_key='k',
                status='active',
            )
        )
        session.commit()
    runtime = SimpleNamespace(
        sandbox=RemoteSandboxServiceInjector(api_url='http://rt', api_key='t')
    )
    with (
        patch('storage.customer_gateway_store.a_session_maker', async_session_maker),
        patch('storage.encrypt_utils.get_jwt_service', return_value=JWT),
        patch.object(gw_service, 'get_global_config', return_value=runtime),
        patch.object(gw_service, '_jwt', return_value=JWT),
    ):
        yield


async def _configure(default_alias: str | None, granted: list[str] | None):
    profile: dict[str, Any] = {'base_url': 'https://gw.example/v1', 'models': CATALOG}
    if default_alias is not None:
        profile['default_alias'] = default_alias
    await CustomerGatewayStore.set_org_gateway(ORG_ID, 'gw', profile, True, 'test')
    if granted is not None:
        await CustomerGatewayStore.set_member_credential(
            ORG_ID, FIRST, 'sk-upstream-secret', granted, 'test'
        )


def _service(saved_llm: dict[str, Any], before_bind=None):
    sandbox = _sandbox(str(FIRST), ORG_ID).model_copy(
        update={'created_at': datetime.now(UTC) + timedelta(minutes=1)}
    )
    svc, client = _build_real_service(sandbox, str(FIRST))

    async def echo(_url, json=None, **_kwargs):
        response = Mock()
        response.json.return_value = {
            'id': str(uuid4()),
            'agent': json['agent'],
            'workspace': json['workspace'],
        }
        return response

    client.post = AsyncMock(side_effect=echo)
    user = UserInfo(id=str(FIRST), agent_settings={'llm': saved_llm})
    svc.user_context.resolver_org_id = ORG_ID
    svc.user_context.get_user_info = AsyncMock(return_value=user)

    async def build(user, _sandbox, _conversation_id, *args, **_kwargs):
        if before_bind is not None:
            await before_bind()
        llm = svc._configure_llm(user, args[5])
        agent = user.agent_settings.model_copy(update={'llm': llm}).create_agent()
        return StartConversationRequest(
            agent=agent, workspace=LocalWorkspace(working_dir='/tmp')
        )

    svc._build_start_conversation_request_for_user = build
    return svc, client.post, user


async def _start(svc, **request: Any):
    return [
        task
        async for task in svc._start_app_conversation(
            AppConversationStartRequest(**request)
        )
    ]


def _posted_agent(post) -> dict[str, Any]:
    assert post.await_count == 1
    return post.await_args.kwargs['json']['agent']


PLACEHOLDER = {'model': default_agent_settings().llm.model}


@pytest.mark.usefixtures('gateway')
async def test_first_member_gets_authorized_default_alias_and_condenser():
    await _configure('deepseek', ['deepseek'])
    svc, post, user = _service(PLACEHOLDER)

    tasks = await _start(svc)

    assert tasks[-1].status.value == 'READY'

    agent = _posted_agent(post)
    for llm, usage_id in (
        (agent['llm'], 'agent'),
        (agent['condenser']['llm'], 'condenser'),
    ):
        assert llm['model'] == 'openai/deepseek'
        assert llm['base_url'] == RELAY
        assert llm['usage_id'] == usage_id
        claims = verify_inference_token(JWT, llm['api_key'])
        assert (claims.user_id, claims.org_id) == (str(FIRST), str(ORG_ID))
    assert 'sk-upstream-secret' not in str(post.await_args.kwargs['json'])
    assert user.agent_settings.llm.model == PLACEHOLDER['model']


@pytest.mark.usefixtures('gateway')
async def test_allowed_saved_model_wins_over_default():
    await _configure('deepseek', ['deepseek', 'sonnet'])
    svc, post, _ = _service({'model': 'sonnet'})

    tasks = await _start(svc)

    assert tasks[-1].status.value == 'READY'
    assert _posted_agent(post)['llm']['model'] == 'openai/sonnet'


@pytest.mark.parametrize(
    ('default_alias', 'granted', 'detail'),
    [
        (None, ['deepseek'], constants.NO_DEFAULT_MODEL_DETAIL),
        ('deepseek', ['sonnet'], "'deepseek' is not granted"),
        ('deepseek', None, 'access denied: no_credential'),
    ],
    ids=['default-missing', 'default-ungranted', 'no-credential'],
)
@pytest.mark.usefixtures('gateway')
async def test_unusable_default_rejected_before_sandbox(default_alias, granted, detail):
    await _configure(default_alias, granted)
    svc, post, _ = _service(PLACEHOLDER)
    svc._wait_for_sandbox_start = AsyncMock(side_effect=AssertionError('allocated'))

    with pytest.raises(HTTPException) as exc_info:
        await _start(svc)

    assert exc_info.value.status_code == 409
    assert detail in exc_info.value.detail
    assert post.await_count == 0


@pytest.mark.usefixtures('gateway')
async def test_explicit_forbidden_model_never_falls_back():
    await _configure('deepseek', ['deepseek'])
    svc, post, _ = _service(PLACEHOLDER)

    tasks = await _start(svc, llm_model='openai/gpt-forbidden')

    assert tasks[-1].status.value == 'ERROR'
    assert 'not in the customer gateway catalog' in tasks[-1].detail
    assert post.await_count == 0


@pytest.mark.usefixtures('gateway')
async def test_gateway_switch_off_keeps_no_default_409_without_org_lookup(monkeypatch):
    await _configure('deepseek', ['deepseek'])
    monkeypatch.delenv('CUSTOMER_GATEWAY_ENABLED')
    svc, post, _ = _service(PLACEHOLDER)
    lookup = AsyncMock(side_effect=AssertionError('credential org lookup'))
    monkeypatch.setattr(live_status, 'get_credential_org_id', lookup)

    with pytest.raises(HTTPException) as exc_info:
        await _start(svc)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == constants.NO_DEFAULT_MODEL_DETAIL
    lookup.assert_not_awaited()
    assert post.await_count == 0


@pytest.mark.usefixtures('gateway')
async def test_inherited_install_default_is_not_replaced_by_alias(monkeypatch):
    await _configure('deepseek', ['deepseek'])
    monkeypatch.setattr(constants, 'LITELLM_DEFAULT_MODEL', 'litellm_proxy/install')
    select = AsyncMock(side_effect=AssertionError('default selected'))
    monkeypatch.setattr(customer_gateway_hooks, 'select_launch_default_llm', select)
    svc, post, _ = _service(PLACEHOLDER)

    tasks = await _start(svc)

    select.assert_not_awaited()
    assert tasks[-1].status.value == 'ERROR'
    assert 'not in the customer gateway catalog' in tasks[-1].detail
    assert post.await_count == 0


@pytest.mark.usefixtures('gateway')
async def test_default_selection_fails_closed_if_gateway_turns_off_before_bind():
    await _configure('deepseek', ['deepseek'])

    async def disable_gateway():
        await CustomerGatewayStore.set_org_gateway(
            ORG_ID,
            'gw',
            {'base_url': 'https://gw.example/v1', 'models': CATALOG},
            False,
            'test',
        )

    svc, post, _ = _service(PLACEHOLDER, before_bind=disable_gateway)
    title_llm = svc._start_title_llm
    svc._start_title_llm = AsyncMock(side_effect=AssertionError('title llm'))

    tasks = await _start(
        svc,
        initial_message=SendMessageRequest(
            role='user', content=[TextContent(text='fix the build')]
        ),
        generate_title_on_submit=True,
    )

    assert tasks[-1].status.value == 'ERROR'
    assert 'no longer bound to that gateway' in tasks[-1].detail
    assert post.await_count == 0
    svc._start_title_llm.assert_not_awaited()
    # Without that guard, the app-server title LLM would route the alias directly.
    alias_user = UserInfo(id=str(FIRST), agent_settings={'llm': {'model': 'deepseek'}})
    svc.user_context.get_provider_connections = AsyncMock(return_value=[])
    direct = await title_llm(alias_user, AppConversationStartRequest())
    assert direct is not None and direct.base_url != RELAY
    assert direct.model.endswith('deepseek')
