"""Drive the enterprise switch_acp_effort route against a real local agent-server.

Usage: enterprise_proxy_check.py <agent-server-url> <session-key-file> <conversation-id> <effort>
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import httpx
from fastapi import HTTPException

from openhands.app_server.app_conversation import app_conversation_router as router
from openhands.app_server.app_conversation.app_conversation_models import (
    SwitchAcpEffortRequest,
)

url, key_file, conversation_id, effort = sys.argv[1:5]
key = open(key_file).read().strip()


async def switch(client: httpx.AsyncClient, ctx, value: str) -> None:
    with patch.object(
        router, "_get_agent_server_context", new=AsyncMock(return_value=ctx)
    ):
        await router.switch_conversation_acp_effort(
            conversation_id=UUID(conversation_id),
            request=SwitchAcpEffortRequest(effort=value),
            app_conversation_service=MagicMock(),
            sandbox_service=MagicMock(),
            sandbox_spec_service=MagicMock(),
            httpx_client=client,
        )


async def main() -> None:
    ctx = MagicMock(agent_server_url=url, session_api_key=key)
    async with httpx.AsyncClient() as client:
        await switch(client, ctx, effort)
        info = (
            await client.get(
                f"{url}/api/conversations/{conversation_id}",
                headers={"X-Session-API-Key": key},
            )
        ).json()
        print(
            "after proxy switch:",
            {"model": info.get("current_model_id"), "effort": info.get("current_effort")},
        )
        try:
            await switch(client, ctx, "not-a-level")
            print("unknown level: accepted (unexpected)")
        except HTTPException as e:
            print("unknown level: HTTPException", e.status_code)


asyncio.run(main())
