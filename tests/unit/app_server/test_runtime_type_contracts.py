"""Gateway validation accepts SDK models and nested JSON settings."""

import pytest

from openhands.app_server.utils.litellm_integration import (
    LiteLLMIntegrationDisabled,
    validate_agent_llm_payload,
    validate_agent_llms,
)
from openhands.sdk.llm import LLM


def test_gateway_guard_handles_sdk_models_and_nested_tool_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('ENABLE_LITELLM', 'false')
    validate_agent_llm_payload({'properties': {'model': {'type': 'string'}}})
    with pytest.raises(LiteLLMIntegrationDisabled):
        validate_agent_llms(LLM(model='openhands/retired'))
    with pytest.raises(LiteLLMIntegrationDisabled):
        validate_agent_llm_payload(
            {'plugin': {'planning': {'llm': {'model': 'openhands/retired'}}}}
        )
