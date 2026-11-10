"""Tasks exposed to external MCP clients under a resource-bound grant.

Reading needs the ``tasks:read`` scope. Starting a task or sending a follow-up
runs an agent with the granting user's access, so it needs ``tasks:write``.
Every tool acts only on conversations the granting user owns in the granted
organization.
"""

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from mcp.types import ToolAnnotations
from redis.asyncio import Redis
from sqlalchemy import select

from integrations.resolver_context import ResolverUserContext
from openhands.agent_server.models import EventSortOrder, SendMessageRequest
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartRequest,
    AppConversationStartTaskStatus,
    AppSendMessageRequest,
    ConversationTrigger,
)
from openhands.app_server.app_conversation.app_conversation_router import (
    send_message_to_conversation,
)
from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    StoredConversationMetadata,
)
from openhands.app_server.config import (
    get_app_conversation_service,
    get_app_conversation_start_task_service,
    get_event_service,
    get_httpx_client,
    get_sandbox_service,
)
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.specifiy_user_context import USER_CONTEXT_ATTR
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.sdk import TextContent
from openhands.sdk.conversation.response_utils import get_agent_final_response
from server.auth.external_mcp_oauth import READ_SCOPE, WRITE_SCOPE
from server.auth.saas_user_auth import SaasUserAuth
from storage.database import a_session_maker
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas

MAX_PROMPT_CHARS = 8000
MAX_RESULT_CHARS = 4000
PENDING_TTL = 86400
_START_TERMINAL = (
    AppConversationStartTaskStatus.READY,
    AppConversationStartTaskStatus.ERROR,
)
# Keep references so the event loop does not drop running start tasks.
_background: set[asyncio.Task] = set()


def _pending_key(conversation_id: UUID) -> str:
    return f'external-mcp-oauth:task:{conversation_id.hex}'


def register_external_mcp_tools(mcp: FastMCP, base_url: str, redis: Redis) -> None:
    app_url = base_url.removesuffix('/external-mcp')

    def principal(scope: str) -> tuple[UUID, UUID, str]:
        """The granting user, organization and client, when the grant has ``scope``."""
        token = get_access_token()
        if (
            token is None
            or token.resource != f'{base_url}/mcp'
            or token.subject is None
            or 'org_id' not in token.claims
        ):
            raise ToolError('OpenHands authorization is required')
        if scope not in token.scopes:
            raise ToolError(f'This connection was not granted {scope}')
        try:
            return UUID(token.subject), UUID(token.claims['org_id']), token.client_id
        except (TypeError, ValueError) as exc:
            raise ToolError('Invalid OpenHands authorization') from exc

    async def user_state(user_id: UUID, org_id: UUID) -> InjectorState:
        """Service context that acts as the granting user in the granted organization."""
        state = InjectorState()
        user_auth = await SaasUserAuth.get_for_user(str(user_id))
        setattr(
            state,
            USER_CONTEXT_ATTR,
            ResolverUserContext(saas_user_auth=user_auth, resolver_org_id=org_id),
        )
        return state

    async def owns(user_id: UUID, org_id: UUID, conversation_id: UUID) -> bool:
        async with a_session_maker() as session:
            row = await session.execute(
                select(StoredConversationMetadata.conversation_id)
                .join(
                    StoredConversationMetadataSaas,
                    StoredConversationMetadata.conversation_id
                    == StoredConversationMetadataSaas.conversation_id,
                )
                .where(
                    StoredConversationMetadata.conversation_id == str(conversation_id),
                    StoredConversationMetadataSaas.user_id == user_id,
                    StoredConversationMetadataSaas.org_id == org_id,
                    StoredConversationMetadata.conversation_version == 'V1',
                    StoredConversationMetadata.deleted_at.is_(None),
                )
            )
            return row.first() is not None

    def task_url(conversation_id: UUID | str) -> str:
        return f'{app_url}/canvas/conversations/{conversation_id}'

    def parse_id(task_id: str) -> UUID:
        try:
            return UUID(task_id)
        except (TypeError, ValueError) as exc:
            raise ToolError('Unknown task') from exc

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def list_my_tasks(limit: int = 10) -> list[dict[str, str | None]]:
        """List your recent OpenHands tasks from the organization you connected."""
        user_id, org_id, _ = principal(READ_SCOPE)
        limit = min(max(limit, 1), 20)
        async with a_session_maker() as session:
            rows = await session.execute(
                select(
                    StoredConversationMetadata.conversation_id,
                    StoredConversationMetadata.title,
                )
                .join(
                    StoredConversationMetadataSaas,
                    StoredConversationMetadata.conversation_id
                    == StoredConversationMetadataSaas.conversation_id,
                )
                .where(
                    StoredConversationMetadataSaas.user_id == user_id,
                    StoredConversationMetadataSaas.org_id == org_id,
                    StoredConversationMetadata.conversation_version == 'V1',
                    StoredConversationMetadata.deleted_at.is_(None),
                    StoredConversationMetadata.parent_conversation_id.is_(None),
                )
                .order_by(StoredConversationMetadata.last_updated_at.desc())
                .limit(limit)
            )
            return [
                {
                    'id': conversation_id,
                    'title': title,
                    'url': task_url(conversation_id),
                }
                for conversation_id, title in rows.all()
            ]

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, openWorldHint=True
        ),
    )
    async def start_task(
        prompt: str, repository: str | None = None, branch: str | None = None
    ) -> dict[str, str]:
        """Start an OpenHands task that runs in the background with your access.

        ``repository`` is ``owner/name`` on a Git provider you have connected, and
        ``branch`` needs a repository. Returns at once with the task ``id``; poll
        ``get_task`` for progress and the result.
        """
        user_id, org_id, client_id = principal(WRITE_SCOPE)
        prompt = prompt.strip()
        if not prompt or len(prompt) > MAX_PROMPT_CHARS:
            raise ToolError(f'prompt must be 1 to {MAX_PROMPT_CHARS} characters')
        if branch and not repository:
            raise ToolError('branch needs a repository')
        conversation_id = uuid4()
        request = AppConversationStartRequest(
            conversation_id=conversation_id,
            initial_message=SendMessageRequest(
                role='user', content=[TextContent(text=prompt)]
            ),
            selected_repository=repository or None,
            selected_branch=branch or None,
            trigger=ConversationTrigger.REMOTE_API_KEY,
            # Marks the conversation as started through this endpoint, and by whom.
            tags={'source': 'mcp', 'mcpclient': client_id[:256]},
        )
        state = await user_state(user_id, org_id)
        first: asyncio.Future = asyncio.get_running_loop().create_future()

        async def run() -> None:
            try:
                async with get_app_conversation_service(state) as service:
                    async for task in service.start_app_conversation(request):
                        if not first.done():
                            first.set_result(task)
                        if task.status in _START_TERMINAL:
                            break
            except Exception as exc:
                logger.exception(
                    'external_mcp:start_task_failed',
                    extra={'conversation_id': str(conversation_id)},
                )
                if not first.done():
                    first.set_exception(exc)

        background = asyncio.create_task(run())
        _background.add(background)
        background.add_done_callback(_background.discard)
        try:
            started = await first
        except Exception as exc:
            raise ToolError('The task could not be started') from exc
        if started.status == AppConversationStartTaskStatus.ERROR:
            raise ToolError(started.detail or 'The task could not be started')
        # Until the conversation is stored, this is the only record that links the
        # returned id to its owner.
        await redis.set(
            _pending_key(conversation_id),
            json.dumps(
                {
                    'start_task_id': str(started.id),
                    'user_id': str(user_id),
                    'org_id': str(org_id),
                }
            ),
            ex=PENDING_TTL,
        )
        logger.info(
            'external_mcp:task_started',
            extra={
                'conversation_id': str(conversation_id),
                'user_id': str(user_id),
                'org_id': str(org_id),
                'mcp_client_id': client_id,
                'has_repository': repository is not None,
            },
        )
        return {
            'id': str(conversation_id),
            'status': 'starting',
            'url': task_url(conversation_id),
        }

    async def pending(
        user_id: UUID, org_id: UUID, conversation_id: UUID, state: InjectorState
    ) -> dict[str, Any] | None:
        """A task this user started here whose conversation is not stored yet."""
        raw = await redis.get(_pending_key(conversation_id))
        if not raw:
            return None
        record = json.loads(raw)
        if record['user_id'] != str(user_id) or record['org_id'] != str(org_id):
            return None
        async with get_app_conversation_start_task_service(state) as service:
            task = await service.get_app_conversation_start_task(
                UUID(record['start_task_id'])
            )
        if task is None or task.created_by_user_id != str(user_id):
            return None
        failed = task.status == AppConversationStartTaskStatus.ERROR
        return {
            'id': str(conversation_id),
            'status': 'error' if failed else 'starting',
            'detail': task.detail if failed else task.status.value.lower(),
            'url': task_url(conversation_id),
        }

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def get_task(task_id: str) -> dict[str, Any]:
        """Get the status of one of your tasks and, once it has finished, its result.

        ``status`` is ``starting``, ``running``, ``waiting_for_confirmation``,
        ``paused``, ``finished``, ``stopped`` or ``error``.
        """
        user_id, org_id, _ = principal(READ_SCOPE)
        conversation_id = parse_id(task_id)
        state = await user_state(user_id, org_id)
        if not await owns(user_id, org_id, conversation_id):
            found = await pending(user_id, org_id, conversation_id, state)
            if found is None:
                raise ToolError('Unknown task')
            return found
        async with get_app_conversation_service(state) as service:
            conversation = await service.get_app_conversation(conversation_id)
        if conversation is None:
            raise ToolError('Unknown task')
        execution = conversation.execution_status
        status = execution.value if execution else 'stopped'
        if status == 'idle':
            status = 'finished'
        result: str | None = None
        if status == 'finished':
            async with get_event_service(state) as events:
                page = await events.search_events(
                    conversation_id,
                    sort_order=EventSortOrder.TIMESTAMP_DESC,
                    limit=50,
                )
            text = get_agent_final_response(list(reversed(page.items)))
            result = text[:MAX_RESULT_CHARS] or None
        return {
            'id': str(conversation_id),
            'title': conversation.title,
            'status': status,
            'sandbox_status': conversation.sandbox_status.value,
            'repository': conversation.selected_repository,
            'branch': conversation.selected_branch,
            'pull_requests': conversation.pr_number,
            'result': result,
            'url': task_url(conversation_id),
        }

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, openWorldHint=True
        ),
    )
    async def send_message(task_id: str, message: str) -> dict[str, str]:
        """Send a follow-up message to one of your running tasks.

        The message is accepted, not answered: poll ``get_task`` for the result.
        """
        user_id, org_id, client_id = principal(WRITE_SCOPE)
        conversation_id = parse_id(task_id)
        message = message.strip()
        if not message or len(message) > MAX_PROMPT_CHARS:
            raise ToolError(f'message must be 1 to {MAX_PROMPT_CHARS} characters')
        if not await owns(user_id, org_id, conversation_id):
            raise ToolError('Unknown task, or it is still starting')
        state = await user_state(user_id, org_id)
        try:
            async with (
                get_app_conversation_service(state) as conversations,
                get_sandbox_service(state) as sandboxes,
                get_httpx_client(state) as httpx_client,
            ):
                await send_message_to_conversation(
                    conversation_id,
                    AppSendMessageRequest(
                        role='user', content=[TextContent(text=message)], run=True
                    ),
                    app_conversation_service=conversations,
                    sandbox_service=sandboxes,
                    httpx_client=httpx_client,
                )
        except HTTPException as exc:
            # The REST handler's detail says why (paused, archived, unreachable).
            raise ToolError(str(exc.detail)) from exc
        logger.info(
            'external_mcp:message_sent',
            extra={
                'conversation_id': str(conversation_id),
                'user_id': str(user_id),
                'org_id': str(org_id),
                'mcp_client_id': client_id,
            },
        )
        return {'id': str(conversation_id), 'status': 'accepted'}
