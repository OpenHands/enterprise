"""Persist known conversation spend while a remote runtime is still reachable."""

import asyncio
from functools import partial
from typing import Any
from uuid import UUID

import httpx

from openhands.app_server.errors import SandboxDeleteRetryError
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.specifiy_user_context import USER_CONTEXT_ATTR
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.docker_utils import (
    replace_localhost_hostname_for_docker,
)
from openhands.sdk import ConversationStats
from openhands.sdk.utils.paging import page_iterator

FINAL_METRICS_TIMEOUT_SECONDS = 10.0


async def persist_sandbox_final_metrics(
    sandbox_id: str,
    runtime: dict[str, Any],
    user_context: UserContext,
    httpx_client: httpx.AsyncClient,
) -> None:
    """Commit current stats before deleting an authorized remote sandbox.

    Keep the caller's identity/organization filters; never elevate to ADMIN.
    Read metadata, release the DB connection, fetch stats, then commit each
    snapshot through the existing idempotent statistics writer. No event replay
    or workspace archive is needed. A failed snapshot leaves teardown retryable
    instead of permanently replacing known spend with the initial zero.
    """
    from openhands.app_server.config import (
        get_app_conversation_info_service,
        get_db_session,
    )

    def scoped_state() -> InjectorState:
        state = InjectorState()
        setattr(state, USER_CONTEXT_ATTR, user_context)
        return state

    try:
        # Bound the whole metadata pass, not five seconds per conversation.
        # This is deliberately separate from potentially long workspace capture.
        async with asyncio.timeout(FINAL_METRICS_TIMEOUT_SECONDS):
            read_state = scoped_state()
            async with (
                get_app_conversation_info_service(read_state) as info_service,
                get_db_session(read_state),
            ):
                conversations = [
                    info
                    async for info in page_iterator(
                        partial(
                            info_service.search_app_conversation_info,
                            sandbox_id__eq=sandbox_id,
                            include_sub_conversations=True,
                        )
                    )
                ]

            runtime_url = replace_localhost_hostname_for_docker(runtime['url'])
            for info in conversations:
                if info.sandbox_id != sandbox_id:
                    raise ValueError('Conversation belongs to another sandbox')
                response = await httpx_client.get(
                    f'{runtime_url.rstrip("/")}/api/conversations/{info.id.hex}',
                    headers={'X-Session-API-Key': runtime['session_api_key']},
                    timeout=5,
                )
                if response.status_code == 404:
                    # Already-deleted conversations have no live stats to fetch;
                    # retain their persisted values rather than writing zero.
                    continue
                response.raise_for_status()
                payload = response.json()
                if UUID(payload['id']) != info.id:
                    raise ValueError('Agent-server conversation id mismatch')
                stats = ConversationStats.model_validate(payload['stats'])
                if not stats.usage_to_metrics:
                    continue
                write_state = scoped_state()
                async with (
                    get_app_conversation_info_service(write_state) as info_service,
                    get_db_session(write_state),
                ):
                    # This writer commits the snapshot and records only positive
                    # per-bucket deltas, so delete retries cannot double charge.
                    await info_service.update_conversation_statistics(info.id, stats)
    except Exception as error:
        raise SandboxDeleteRetryError(
            f'Could not persist final metrics for sandbox {sandbox_id}'
        ) from error
