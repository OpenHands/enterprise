"""Persist known conversation spend while a remote runtime is still reachable."""

import asyncio
import logging
from functools import partial
from typing import Any

import httpx

from openhands.agent_server.models import ConversationInfo
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
    AppConversationSortOrder,
)
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.specifiy_user_context import USER_CONTEXT_ATTR
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.docker_utils import (
    replace_localhost_hostname_for_docker,
)
from openhands.sdk.utils.paging import page_iterator

_logger = logging.getLogger(__name__)
FINAL_METRICS_TIMEOUT_SECONDS = 10.0
FINAL_METRICS_MAX_CONCURRENCY = 8


async def persist_sandbox_final_metrics(
    sandbox_id: str,
    runtime: dict[str, Any],
    user_context: UserContext,
    httpx_client: httpx.AsyncClient,
) -> bool:
    """Commit current stats before deleting an authorized remote sandbox.

    Inherit the caller's identity/organization filters without elevation.
    Read metadata, release the DB connection, fetch stats, then commit each
    snapshot through the existing idempotent statistics writer. No event replay
    or workspace archive is needed. Return False on failure, preserving the last
    saved totals. Each conversation fails independently, and bounded concurrent
    requests keep a slow child from serially delaying its parent. Metrics must
    never prevent stopping an unhealthy runtime.
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
                            sort_order=AppConversationSortOrder.CREATED_AT,
                        )
                    )
                ]

            runtime_url = replace_localhost_hostname_for_docker(runtime['url'])
            slots = asyncio.Semaphore(FINAL_METRICS_MAX_CONCURRENCY)

            async def persist_conversation(info: AppConversationInfo) -> bool:
                try:
                    async with slots:
                        response = await httpx_client.get(
                            f'{runtime_url.rstrip("/")}/api/conversations/{info.id.hex}',
                            headers={'X-Session-API-Key': runtime['session_api_key']},
                            timeout=5,
                        )
                        if response.status_code == 404:
                            # Preserve saved totals when the conversation is gone.
                            _logger.info(
                                'Live metrics unavailable for deleted conversation %s in sandbox %s',
                                info.id,
                                sandbox_id,
                            )
                            return True
                        response.raise_for_status()
                        live_info = ConversationInfo.model_validate(response.json())
                        if live_info.id != info.id:
                            raise ValueError('Agent-server conversation id mismatch')
                        if not live_info.stats.usage_to_metrics:
                            return True
                        write_state = scoped_state()
                        async with (
                            get_app_conversation_info_service(
                                write_state
                            ) as info_service,
                            get_db_session(write_state),
                        ):
                            # Each task owns its session. The existing writer commits
                            # only positive per-bucket deltas, including on retries.
                            await info_service.update_conversation_statistics(
                                info.id, live_info.stats
                            )
                    return True
                except Exception:
                    _logger.warning(
                        'Could not persist final metrics for conversation %s in sandbox %s; continuing teardown',
                        info.id,
                        sandbox_id,
                        exc_info=True,
                    )
                    return False

            results = await asyncio.gather(
                *(persist_conversation(info) for info in conversations)
            )
        return all(results)
    except Exception:
        _logger.warning(
            'Could not persist final metrics for sandbox %s; continuing teardown',
            sandbox_id,
            exc_info=True,
        )
        return False
