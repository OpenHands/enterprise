import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

from openhands.sdk import LLM, Tool
from openhands.sdk.launch import LaunchRuntime, ResolvedLaunch, finalize
from openhands.sdk.settings import OpenHandsAgentSettings, validate_agent_settings
from tests.unit.app_server.test_live_status_app_conversation_service import (
    TestLiveStatusAppConversationService,
)


async def main():
    cases = []
    for selected in ([], ['terminal', 'file_editor', 'task_tracker']):
        fixture = TestLiveStatusAppConversationService()
        fixture.setup_method()
        service = fixture.service
        service.web_url = None
        service._setup_secrets_for_git_providers = AsyncMock(return_value={})
        llm = LLM(model='gpt-4', api_key='fixture-not-a-real-key')
        service._configure_llm_and_mcp = AsyncMock(return_value=(llm, {}))
        service._resolve_registered_marketplaces = AsyncMock(return_value=None)
        fixture.mock_user.agent_settings = OpenHandsAgentSettings(
            llm=llm, tools=[Tool(name=name) for name in selected]
        )
        fixture.mock_user.active_agent_profile_id = str(uuid4())
        result = await service._build_start_conversation_request_for_user(
            user=fixture.mock_user,
            sandbox=fixture.mock_sandbox,
            conversation_id=uuid4(),
            initial_message=None,
            system_message_suffix=None,
            git_provider=None,
            working_dir='/tmp',
            remote_workspace=None,
        )
        source = result.agent
        if source is None:
            source = ResolvedLaunch(
                settings=validate_agent_settings(result.agent_settings)
            )
        agent = finalize(
            source,
            LaunchRuntime(
                browser_available=True, acp_skill_sourcing='openhands_managed'
            ),
            additions=result.agent_launch_additions,
        ).agent
        actual = [tool.name for tool in agent.tools]
        cases.append(
            {
                'selected': selected,
                'actual': actual,
                'preserved': actual == selected,
                'browser_added': 'browser_tool_set' in actual,
            }
        )
    Path(sys.argv[1]).write_text(
        json.dumps({'checkout': str(Path.cwd()), 'cases': cases}, indent=2) + '\n'
    )
    print(json.dumps(cases))


asyncio.run(main())
