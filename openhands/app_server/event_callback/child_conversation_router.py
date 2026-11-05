"""Launcher behind the ``start_child_conversation`` tool for Cloud parents.

The agent-server inside a parent's sandbox posts here to start a child
conversation on this backend. The route lives in its own module because it
needs the child-launch contract (``StartChildConversationRequest`` /
``StartChildConversationResponse``) that only newer ``openhands-sdk``
releases provide; ``v1_router`` includes it when that import succeeds and
leaves it out otherwise, so the app server still boots on an older pin.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from openhands.agent_server.models import (
    StartChildConversationRequest,
    StartChildConversationResponse,
    TextContent,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartRequest,
    AppConversationStartTask,
    AppConversationStartTaskStatus,
    SendMessageRequest,
)
from openhands.app_server.config import (
    get_app_conversation_info_service,
    get_app_conversation_service,
    get_global_config,
)
from openhands.app_server.errors import AuthError
from openhands.app_server.event_callback.webhook_router import (
    _resolve_user_context,
    valid_sandbox,
)
from openhands.app_server.sandbox.sandbox_models import SandboxRecord
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.specifiy_user_context import (
    ADMIN,
    USER_CONTEXT_ATTR,
)

# Same prefix as the webhook router: the SaaS auth middleware exempts
# ``/api/v1/webhooks/`` because these routes authenticate with the sandbox key.
router = APIRouter(prefix='/webhooks', tags=['Webhooks'])


async def _resolve_conversation_org_id(conversation_id: UUID) -> UUID | None:
    """Organization that owns *conversation_id* in SaaS; ``None`` in OSS mode.

    Imported lazily, like the daily-quota helpers: the SaaS storage layer only
    exists in the enterprise deployment.
    """
    try:
        from sqlalchemy import select

        from storage.database import a_session_maker
        from storage.stored_conversation_metadata_saas import (
            StoredConversationMetadataSaas,
        )
    except ImportError:
        return None

    async with a_session_maker() as session:
        result = await session.execute(
            select(StoredConversationMetadataSaas.org_id).where(
                StoredConversationMetadataSaas.conversation_id == str(conversation_id)
            )
        )
        return result.scalar_one_or_none()


@router.post(
    '/conversations/{conversation_id}/children',
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {'description': 'Parent conversation not found'},
        500: {'description': 'Child conversation failed to start'},
    },
)
async def start_child_conversation(
    conversation_id: UUID,
    request: StartChildConversationRequest,
    sandbox_record: SandboxRecord = Depends(valid_sandbox),
) -> StartChildConversationResponse:
    """Start a child of ``conversation_id`` for the sandbox that calls.

    The child is provisioned through the normal app-conversation lifecycle as
    the parent's owner, in the parent's organization, and linked via
    ``parent_conversation_id``; sandbox, repository, branch and model are
    inherited from the parent. ``isolation`` is not applicable here: children
    share the parent's sandbox and its workspace layout.
    """
    state = InjectorState()
    setattr(state, USER_CONTEXT_ATTR, ADMIN)
    async with get_app_conversation_info_service(state) as info_service:
        parent = await info_service.get_app_conversation_info(conversation_id)
    if parent is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail='Parent conversation not found'
        )
    if (
        parent.created_by_user_id != sandbox_record.created_by_user_id
        or parent.sandbox_id != sandbox_record.id
    ):
        raise AuthError()

    user_context = await _resolve_user_context(parent.created_by_user_id)
    # Scope the launch to the parent's organization rather than the owner's
    # currently selected one, so the lookup, quota and SaaS metadata all agree.
    org_id = await _resolve_conversation_org_id(parent.id)
    set_org_override = getattr(
        user_context.user_auth, 'set_effective_org_id_override', None
    )
    if org_id is not None and callable(set_org_override):
        set_org_override(org_id)

    start_request = AppConversationStartRequest(
        parent_conversation_id=parent.id,
        # Not inherited by the start flow; without it the owner's bearer-style
        # auth would record the child as an API-triggered conversation.
        trigger=parent.trigger,
        initial_message=SendMessageRequest(
            role='user', content=[TextContent(text=request.task)]
        ),
        title=request.title,
    )
    start_state = InjectorState()
    setattr(start_state, USER_CONTEXT_ATTR, user_context)
    task: AppConversationStartTask | None = None
    async with get_app_conversation_service(start_state) as app_conversation_service:
        async for task in app_conversation_service.start_app_conversation(
            start_request
        ):
            if task.status == AppConversationStartTaskStatus.ERROR:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=task.detail or 'Child conversation failed to start',
                )
    if task is None or task.app_conversation_id is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Child conversation did not become ready',
        )

    web_url = get_global_config().web_url
    return StartChildConversationResponse(
        conversation_id=task.app_conversation_id,
        parent_conversation_id=parent.id,
        status=task.status.value,
        title=request.title,
        url=f'{web_url}/conversations/{task.app_conversation_id}' if web_url else None,
    )
