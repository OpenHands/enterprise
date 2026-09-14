"""Actual validated user and SDK settings for conversation service tests."""

from fastmcp.mcp_config import MCPConfig
from pydantic import SecretStr

from openhands.app_server.settings.settings_models import SandboxGroupingStrategy
from openhands.app_server.user.user_models import UserInfo
from openhands.sdk.llm import LLM
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config
from openhands.sdk.settings import ConversationSettings, OpenHandsAgentSettings
from openhands.sdk.settings.model import SecurityAnalyzerType


def normalize_fixture_mcp_servers(config: MCPConfig) -> dict[str, MCPServer]:
    return coerce_mcp_config(config)


def make_user_info(
    *,
    id: str | None = None,
    llm_model: str = 'openai/gpt-4o',
    llm_base_url: str | None = None,
    llm_api_key: str | SecretStr | None = None,
    sandbox_grouping_strategy: SandboxGroupingStrategy = SandboxGroupingStrategy.NO_GROUPING,
    confirmation_mode: bool = False,
    security_analyzer: SecurityAnalyzerType | None = None,
    search_api_key: SecretStr | None = None,
    mcp_config: MCPConfig | None = None,
    disabled_skills: list[str] | None = None,
    git_full_clone: bool = False,
    title_llm_profile: str | None = None,
    active_agent_profile_id: str | None = None,
    active_agent_profile_revision: int | None = None,
) -> UserInfo:
    return UserInfo(
        id=id,
        agent_settings=OpenHandsAgentSettings(
            llm=LLM(
                model=llm_model or 'openai/gpt-4o',
                api_key=llm_api_key,
                base_url=llm_base_url,
            ),
            mcp_config=normalize_fixture_mcp_servers(mcp_config) if mcp_config else {},
        ),
        conversation_settings=ConversationSettings(
            confirmation_mode=confirmation_mode, security_analyzer=security_analyzer
        ),
        sandbox_grouping_strategy=sandbox_grouping_strategy,
        search_api_key=search_api_key,
        disabled_skills=disabled_skills,
        git_full_clone=git_full_clone,
        title_llm_profile=title_llm_profile,
        active_agent_profile_id=active_agent_profile_id,
        active_agent_profile_revision=active_agent_profile_revision,
    )
