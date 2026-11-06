"""Managed conversation forks: branch a conversation at one of its events.

The agent server in the source's sandbox does the fork (``POST
/api/conversations/{id}/fork``). The app server owns the result: it records the
fork as a conversation of the same user and org, in the same sandbox and
workspace, and copies the branched history into its event store so the fork
can be listed and read after the sandbox pauses.
"""

import asyncio
import logging
from uuid import UUID, uuid4

import httpx

from openhands.agent_server.models import EventPage
from openhands.app_server.app_conversation.app_conversation_info_service import (
    AppConversationInfoService,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    ACP_SERVER_TAG_KEY,
    AGENT_PROFILE_ID_TAG_KEY,
    AGENT_PROFILE_REVISION_TAG_KEY,
    ARCHIVE_WORKSPACE_PATH_TAG_KEY,
    FORKED_FROM_CONVERSATION_ID_TAG_KEY,
    FORKED_FROM_EVENT_ID_TAG_KEY,
    AppConversationForkRequest,
    AppConversationInfo,
)
from openhands.app_server.event.event_service import EventService

logger = logging.getLogger(__name__)

FORK_TITLE_SUFFIX = ' (branch)'
_MAX_TITLE_LENGTH = 200

# Only tags that describe how the conversation runs carry over. Automation
# context, pins and other caller tags belong to the source.
_INHERITED_TAG_KEYS = (
    ACP_SERVER_TAG_KEY,
    AGENT_PROFILE_ID_TAG_KEY,
    AGENT_PROFILE_REVISION_TAG_KEY,
    ARCHIVE_WORKSPACE_PATH_TAG_KEY,
)


class ConversationForkError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _default_fork_title(source_title: str | None) -> str:
    base = source_title or 'Conversation'
    return base[: _MAX_TITLE_LENGTH - len(FORK_TITLE_SUFFIX)] + FORK_TITLE_SUFFIX


def build_fork_info(
    source: AppConversationInfo, fork_id: UUID, request: AppConversationForkRequest
) -> AppConversationInfo:
    tags = {key: source.tags[key] for key in _INHERITED_TAG_KEYS if key in source.tags}
    tags[FORKED_FROM_CONVERSATION_ID_TAG_KEY] = source.id.hex
    tags[FORKED_FROM_EVENT_ID_TAG_KEY] = request.from_event_id
    return AppConversationInfo(
        id=fork_id,
        created_by_user_id=source.created_by_user_id,
        sandbox_id=source.sandbox_id,
        selected_repository=source.selected_repository,
        selected_branch=source.selected_branch,
        git_provider=source.git_provider,
        additional_repositories=list(source.additional_repositories),
        title=request.title or _default_fork_title(source.title),
        trigger=source.trigger,
        llm_model=source.llm_model,
        agent_kind=source.agent_kind,
        tags=tags,
    )


async def fork_app_conversation(
    source: AppConversationInfo,
    request: AppConversationForkRequest,
    agent_server_url: str,
    session_api_key: str | None,
    app_conversation_info_service: AppConversationInfoService,
    event_service: EventService,
    httpx_client: httpx.AsyncClient,
) -> UUID:
    """Fork ``source`` at ``request.from_event_id`` and return the fork's id.

    Raises ConversationForkError; on failure no fork row or agent-server
    conversation is left behind.
    """
    fork_info = build_fork_info(source, uuid4(), request)
    fork_id = fork_info.id
    headers = {'X-Session-API-Key': session_api_key} if session_api_key else {}
    conversations_url = f'{agent_server_url}/api/conversations'

    # Saved before the agent server starts the fork, so the fork's webhooks find
    # an owned row instead of creating an untitled stub.
    await app_conversation_info_service.save_app_conversation_info(fork_info)

    async def rollback() -> None:
        try:
            await httpx_client.delete(
                f'{conversations_url}/{fork_id.hex}', headers=headers, timeout=30.0
            )
        except httpx.HTTPError:
            logger.warning('Failed to delete agent-server fork %s', fork_id)
        await app_conversation_info_service.delete_app_conversation_info(fork_id)

    try:
        response = await httpx_client.post(
            f'{conversations_url}/{source.id.hex}/fork',
            json={
                'id': str(fork_id),
                'title': fork_info.title,
                'from_event_id': request.from_event_id,
                'tags': {
                    FORKED_FROM_CONVERSATION_ID_TAG_KEY: source.id.hex,
                    FORKED_FROM_EVENT_ID_TAG_KEY: request.from_event_id,
                },
            },
            headers=headers,
            timeout=60.0,
        )
    except httpx.RequestError as e:
        await rollback()
        raise ConversationForkError(502, 'Failed to reach agent server.') from e

    if response.status_code == 404:
        await rollback()
        raise ConversationForkError(
            404,
            f'Event {request.from_event_id} not found in conversation {source.id}',
        )
    if response.is_error:
        logger.error(
            'Agent server fork failed: %s - %s', response.status_code, response.text
        )
        await rollback()
        raise ConversationForkError(502, f'Agent server error: {response.status_code}')

    try:
        await _copy_history(
            f'{conversations_url}/{fork_id.hex}/events/search',
            headers,
            fork_id,
            event_service,
            httpx_client,
        )
    except Exception as e:
        logger.exception('Failed to copy history of fork %s', fork_id)
        await rollback()
        raise ConversationForkError(
            502, 'Failed to copy the branch history from the agent server.'
        ) from e

    return fork_id


async def _copy_history(
    search_url: str,
    headers: dict[str, str],
    fork_id: UUID,
    event_service: EventService,
    httpx_client: httpx.AsyncClient,
) -> None:
    # Webhooks only deliver events created after the fork, so the copied
    # history is read back from the agent server. No callbacks run for it: they
    # already ran on the source.
    page_id: str | None = None
    while True:
        params: dict[str, str | int] = {'limit': 100}
        if page_id:
            params['page_id'] = page_id
        response = await httpx_client.get(
            search_url, params=params, headers=headers, timeout=30.0
        )
        response.raise_for_status()
        page = EventPage.model_validate(response.json())
        await asyncio.gather(
            *(event_service.save_event(fork_id, event) for event in page.items)
        )
        page_id = page.next_page_id
        if not page_id:
            return
