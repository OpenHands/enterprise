import asyncio
import logging
from functools import partial
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, Field

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
    AppConversationSortOrder,
)
from openhands.app_server.event.event_stream import SequencedEvent, parse_stream_entries
from openhands.app_server.event.google_cloud_event_service import (
    GoogleCloudEventService,
)
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.specifiy_user_context import (
    USER_CONTEXT_ATTR,
    SandboxUserContext,
)
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.docker_utils import (
    replace_localhost_hostname_for_docker,
)
from openhands.sdk.utils.paging import page_iterator

_logger = logging.getLogger(__name__)
FINAL_EVENTS_TIMEOUT_SECONDS = 10


class ReplayPage(BaseModel):
    through_seq: int = Field(ge=-1, le=2**53 - 1, strict=True)
    events: list[SequencedEvent] = Field(max_length=100)


async def _reconcile_tail(
    conversation_id: UUID,
    runtime_url: str,
    session_api_key: str,
    http_client: httpx.AsyncClient,
    event_store: GoogleCloudEventService,
) -> int:
    url = f'{runtime_url.rstrip("/")}/api/conversations/{conversation_id.hex}/events/webhook-replay'
    after = await event_store.get_event_stream_ack(conversation_id)
    through = None
    while through is None or after < through:
        params = {'after_seq': after}
        if through is not None:
            params['through_seq'] = through
        response = await http_client.get(
            url,
            headers={'X-Session-API-Key': session_api_key},
            params=params,
            timeout=5,
        )
        response.raise_for_status()
        page = ReplayPage.model_validate(response.json())
        if through is not None and page.through_seq != through:
            raise ValueError('Snapshot sequence changed between pages')
        through = page.through_seq
        entries = parse_stream_entries(page.events, after + 1)
        covered = entries[-1][0] if entries else after
        if covered > through or not entries and covered != through:
            raise ValueError('Incomplete snapshot page')
        if entries:
            await event_store.save_event_stream_batch(conversation_id, entries)
        after = covered
    return after


# TODO: Remove legacy tail backfill after verified handoff and the rollback window.
# https://github.com/OpenHands/software-agent-sdk/issues/7036
async def persist_sandbox_final_events(
    sandbox_id: str,
    runtime: dict[str, Any],
    user_context: UserContext,
    http_client: httpx.AsyncClient,
) -> None:
    from openhands.app_server.config import (
        get_app_conversation_info_service,
        get_db_session,
        get_event_service,
    )

    def scoped_state(context: UserContext) -> InjectorState:
        state = InjectorState()
        setattr(state, USER_CONTEXT_ATTR, context)
        return state

    try:
        async with asyncio.timeout(FINAL_EVENTS_TIMEOUT_SECONDS):
            state = scoped_state(user_context)
            async with (
                get_app_conversation_info_service(state) as info,
                get_db_session(state),
            ):
                conversations = [
                    item
                    async for item in page_iterator(
                        partial(
                            info.search_app_conversation_info,
                            sandbox_id__eq=sandbox_id,
                            include_sub_conversations=True,
                            sort_order=AppConversationSortOrder.CREATED_AT,
                        )
                    )
                ]

            runtime_url = replace_localhost_hostname_for_docker(runtime['url'])
            slots = asyncio.Semaphore(8)

            async def reconcile(conversation: AppConversationInfo) -> None:
                context = user_context
                if conversation.created_by_user_id:
                    context = SandboxUserContext(
                        user_id=conversation.created_by_user_id, sandbox_id=sandbox_id
                    )
                try:
                    async with (
                        slots,
                        get_event_service(scoped_state(context)) as event_store,
                    ):
                        if not isinstance(event_store, GoogleCloudEventService):
                            return
                        through = await _reconcile_tail(
                            conversation.id,
                            runtime_url,
                            runtime['session_api_key'],
                            http_client,
                            event_store,
                        )
                        _logger.info(
                            'final_event_snapshot_stored sandbox_id=%s conversation_id=%s through_seq=%s',
                            sandbox_id,
                            conversation.id,
                            through,
                        )
                except Exception as exc:
                    _logger.warning(
                        'final_event_reconciliation_failed sandbox_id=%s conversation_id=%s error=%s',
                        sandbox_id,
                        conversation.id,
                        type(exc).__name__,
                    )

            await asyncio.gather(*(reconcile(info) for info in conversations))
    except Exception as exc:
        _logger.warning(
            'sandbox_event_reconciliation_failed sandbox_id=%s error=%s',
            sandbox_id,
            type(exc).__name__,
        )
