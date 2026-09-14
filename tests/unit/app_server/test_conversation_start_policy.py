"""The HTTP launch routes reject disabled gateway settings before side effects."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.models import StartConversationRequest
from openhands.app_server.app_conversation import app_conversation_router as module
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartRequest,
    AppConversationStartTask,
    AppConversationStartTaskStatus,
)
from openhands.app_server.user.user_models import UserInfo
from openhands.app_server.utils.dependencies import check_session_api_key
from openhands.app_server.utils.litellm_integration import (
    LiteLLMIntegrationDisabled,
    validate_agent_llms,
)
from openhands.sdk import Agent, LocalWorkspace
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.llm import LLM
from openhands.sdk.settings import OpenHandsAgentSettings


def _constant_dependency[T](value: T) -> Callable[[], T]:
    def dependency() -> T:
        return value

    return dependency


@dataclass
class LaunchFixture:
    client: TestClient
    context: MagicMock
    service: MagicMock
    reserve: AsyncMock
    release: AsyncMock
    db_session: AsyncMock
    httpx_client: AsyncMock
    user: UserInfo


@pytest.fixture
def launch(monkeypatch: pytest.MonkeyPatch) -> LaunchFixture:
    monkeypatch.setenv('ENABLE_LITELLM', 'false')
    context = MagicMock()
    user = UserInfo(
        id='test-user',
        agent_settings=OpenHandsAgentSettings(
            llm=LLM(model='openai/gpt-4o', api_key=None)
        ),
    )
    context.get_user_info = AsyncMock(return_value=user)
    context.get_user_id = AsyncMock(return_value='test-user')
    context.get_effective_org_id = AsyncMock(return_value=None)
    service = MagicMock()
    db_session = AsyncMock()
    httpx_client = AsyncMock()
    reserve = AsyncMock(return_value=True)
    release = AsyncMock()
    monkeypatch.setattr(module, '_reserve_daily_conversation_quota', reserve)
    monkeypatch.setattr(module, '_release_daily_conversation_quota', release)
    monkeypatch.setattr(module, 'get_analytics_service', lambda: None)

    app = FastAPI()
    app.include_router(module.router, prefix='/api/v1')
    app.dependency_overrides[check_session_api_key] = lambda: None
    for dependency, value in (
        (module.user_context_dependency.dependency, context),
        (module.get_secrets_store, MagicMock()),
        (module.db_session_dependency.dependency, db_session),
        (module.httpx_client_dependency.dependency, httpx_client),
        (module.app_conversation_service_dependency.dependency, service),
    ):
        app.dependency_overrides[dependency] = _constant_dependency(value)
    return LaunchFixture(
        user=user,
        client=TestClient(app),
        context=context,
        service=service,
        reserve=reserve,
        release=release,
        db_session=db_session,
        httpx_client=httpx_client,
    )


@pytest.mark.parametrize('endpoint', ['', '/stream-start'])
@pytest.mark.parametrize('source', ['request', 'saved'])
def test_disabled_model_returns_422_before_quota_or_allocation(
    launch: LaunchFixture, endpoint: str, source: str
) -> None:
    body = {}
    settings = launch.user.agent_settings
    assert settings.agent_kind == 'openhands'
    if source == 'request':
        body['llm_model'] = 'openhands/claude-sonnet-4-5'
    else:
        settings.llm = LLM(model='openhands/claude-sonnet-4-5')
    response = launch.client.post('/api/v1/app-conversations' + endpoint, json=body)
    assert response.status_code == 422
    assert 'Select a direct provider model' in response.json()['detail']
    launch.reserve.assert_not_awaited()
    launch.service.start_app_conversation.assert_not_called()


def test_service_policy_error_returns_422_and_releases_reserved_quota(
    launch: LaunchFixture,
) -> None:
    async def tasks(
        request: AppConversationStartRequest,
    ) -> AsyncIterator[AppConversationStartTask]:
        # The service also validates inherited parent settings before allocation.
        if request.sandbox_id is None:
            raise LiteLLMIntegrationDisabled('Select a direct provider model.')
        yield AppConversationStartTask(created_by_user_id='test-user', request=request)

    launch.service.start_app_conversation.side_effect = tasks
    response = launch.client.post('/api/v1/app-conversations', json={})
    assert response.status_code == 422
    assert response.json()['detail'] == 'Select a direct provider model.'
    launch.release.assert_awaited_once_with('test-user')
    launch.db_session.close.assert_awaited_once()
    launch.httpx_client.aclose.assert_awaited_once()


@pytest.mark.parametrize('key', [None, SecretStr('personal-provider-key')])
def test_direct_endpoint_with_optional_credentials_remains_accepted(
    launch: LaunchFixture, key: SecretStr | None
) -> None:
    launch.user.agent_settings.llm = LLM(
        model='openai/local-model', base_url='http://model-fixture:8000/v1', api_key=key
    )

    async def tasks(
        request: AppConversationStartRequest,
    ) -> AsyncIterator[AppConversationStartTask]:
        yield AppConversationStartTask(
            created_by_user_id='test-user',
            request=request,
            status=AppConversationStartTaskStatus.READY,
        )

    launch.service.start_app_conversation.side_effect = tasks
    response = launch.client.post('/api/v1/app-conversations', json={})
    assert response.status_code == 200
    launch.reserve.assert_awaited_once()


def test_disabled_condenser_model_is_rejected_on_valid_sdk_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('ENABLE_LITELLM', 'false')
    agent = Agent(
        llm=LLM(model='openai/gpt-4o'),
        condenser=LLMSummarizingCondenser(llm=LLM(model='openhands/claude-sonnet-4-5')),
    )
    with pytest.raises(LiteLLMIntegrationDisabled):
        validate_agent_llms(
            StartConversationRequest(
                agent=agent, workspace=LocalWorkspace(working_dir='/workspace')
            )
        )
