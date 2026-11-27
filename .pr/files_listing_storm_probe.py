"""Fire concurrent /files listings at a fake agent-server and count upstream `find` runs.

Run from the repo root: PYTHONPATH=. uv run python .pr/files_listing_storm_probe.py
"""

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI

from openhands.app_server.app_conversation import app_conversation_router as router
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
)
from openhands.app_server.sandbox.sandbox_models import SandboxStatus

PORT = 18777
REQUESTS = 50
stats = {'calls': 0, 'inflight': 0, 'peak': 0}
agent_server = FastAPI()


@agent_server.post('/api/bash/execute_bash_command')
async def execute_bash_command(body: dict):
    stats['calls'] += 1
    stats['inflight'] += 1
    stats['peak'] = max(stats['peak'], stats['inflight'])
    await asyncio.sleep(1.0)
    stats['inflight'] -= 1
    return {'exit_code': 0, 'stdout': './src/app.py\n./README.md\n', 'stderr': ''}


def serve() -> uvicorn.Server:
    server = uvicorn.Server(
        uvicorn.Config(agent_server, port=PORT, log_level='warning')
    )
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server


async def main() -> None:
    conversation_id = uuid4()
    spec = MagicMock()
    spec.working_dir = '/workspace/project'
    ctx = router.AgentServerContext(
        conversation=AppConversationInfo(
            id=conversation_id, created_by_user_id='u', sandbox_id='s'
        ),
        sandbox=MagicMock(status=SandboxStatus.RUNNING),
        sandbox_spec=spec,
        agent_server_url=f'http://127.0.0.1:{PORT}',
        session_api_key='k',
    )
    single_flight = hasattr(router, '_shared_workspace_listing')
    async with httpx.AsyncClient() as client:
        with patch.object(
            router, '_get_agent_server_context', new=AsyncMock(return_value=ctx)
        ):
            started = time.monotonic()
            results = await asyncio.gather(
                *(
                    router.list_conversation_files(
                        conversation_id=conversation_id,
                        path='/workspace/project',
                        app_conversation_service=MagicMock(),
                        sandbox_service=MagicMock(),
                        sandbox_spec_service=MagicMock(),
                        httpx_client=client,
                    )
                    for _ in range(REQUESTS)
                )
            )
            elapsed = time.monotonic() - started
    assert all(r == ['src/app.py', 'README.md'] for r in results)
    print(
        f'single_flight={single_flight} requests={REQUESTS} '
        f'responses_ok={len(results)} upstream_find_calls={stats["calls"]} '
        f'peak_concurrent_finds={stats["peak"]} elapsed={elapsed:.2f}s'
    )


if __name__ == '__main__':
    server = serve()
    asyncio.run(main())
    server.should_exit = True
