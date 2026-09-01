"""Sandboxed Conversation router for OpenHands App Server."""

import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, AsyncGenerator, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.agent_server.models import Success
from openhands.analytics import get_analytics_service, resolve_analytics_context
from openhands.app_server.app_conversation.app_conversation_info_service import (
    AppConversationInfoService,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    ARCHIVE_WORKSPACE_PATH_TAG_KEY,
    AppConversation,
    AppConversationInfo,
    AppConversationPage,
    AppConversationStartRequest,
    AppConversationStartTask,
    AppConversationStartTaskPage,
    AppConversationStartTaskSortOrder,
    AppConversationUpdateRequest,
    AppSendMessageRequest,
    AppSendMessageResponse,
    GetHooksResponse,
    HookDefinitionResponse,
    HookEventResponse,
    HookMatcherResponse,
    SkillResponse,
    SwitchAcpModelRequest,
    SwitchProfileRequest,
)
from openhands.app_server.app_conversation.app_conversation_service import (
    AppConversationService,
    ConversationExportAlreadyRunning,
    ConversationExportLockUnavailable,
    ConversationExportTooLarge,
)
from openhands.app_server.app_conversation.app_conversation_service_base import (
    AppConversationServiceBase,
    get_project_dir,
)
from openhands.app_server.app_conversation.app_conversation_start_task_service import (
    AppConversationStartTaskService,
)
from openhands.app_server.config import (
    depends_app_conversation_info_service,
    depends_app_conversation_service,
    depends_app_conversation_start_task_service,
    depends_db_session,
    depends_httpx_client,
    depends_sandbox_service,
    depends_sandbox_spec_service,
    depends_user_context,
    get_app_conversation_service,
)
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    SandboxInfo,
    SandboxStatus,
)
from openhands.app_server.sandbox.sandbox_service import SandboxService
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.sandbox.sandbox_spec_service import SandboxSpecService
from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.secrets.secrets_store import SecretsStore
from openhands.app_server.services.db_session_injector import set_db_session_keep_open
from openhands.app_server.services.httpx_client_injector import (
    set_httpx_client_keep_open,
)
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.settings.llm_profiles import resolve_profile_llm
from openhands.app_server.settings.marketplace_composition import (
    resolve_registered_marketplaces,
)
from openhands.app_server.settings.settings_models import Settings
from openhands.app_server.settings.settings_router import LITE_LLM_API_URL
from openhands.app_server.user.specifiy_user_context import USER_CONTEXT_ATTR
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.user_auth import get_secrets_store, get_user_settings
from openhands.app_server.utils.dependencies import get_dependencies
from openhands.app_server.utils.docker_utils import (
    replace_localhost_hostname_for_docker,
)
from openhands.sdk.agent.acp_file_credentials import is_valid_codex_auth
from openhands.sdk.settings import ACPAgentSettings
from openhands.sdk.skills import KeywordTrigger, TaskTrigger
from openhands.sdk.workspace.remote.async_remote_workspace import AsyncRemoteWorkspace

# Handle anext compatibility for Python < 3.10
if sys.version_info >= (3, 10):
    from builtins import anext
else:

    async def anext(async_iterator):
        """Compatibility function for anext in Python < 3.10"""
        return await async_iterator.__anext__()


# We use the get_dependencies method here to signal to the OpenAPI docs that this endpoint
# is protected. The actual protection is provided by SetAuthCookieMiddleware
router = APIRouter(
    prefix='/app-conversations', tags=['Conversations'], dependencies=get_dependencies()
)
logger = logging.getLogger(__name__)
app_conversation_service_dependency = depends_app_conversation_service()
app_conversation_info_service_dependency = depends_app_conversation_info_service()
app_conversation_start_task_service_dependency = (
    depends_app_conversation_start_task_service()
)
user_context_dependency = depends_user_context()
db_session_dependency = depends_db_session()
httpx_client_dependency = depends_httpx_client()
sandbox_service_dependency = depends_sandbox_service()
sandbox_spec_service_dependency = depends_sandbox_spec_service()


def _custom_secret_value(secrets: Secrets | None, name: str) -> str | None:
    custom_secret = secrets.custom_secrets.get(name) if secrets else None
    if custom_secret is None:
        return None
    return custom_secret.secret.get_secret_value()


def _has_api_key(value: str | None) -> bool:
    return bool(value and value.strip())


def _request_or_stored_secret_value(
    secrets: Secrets | None,
    request_secrets: dict[str, SecretStr],
    name: str,
) -> str | None:
    if name in request_secrets:
        return request_secrets[name].get_secret_value()
    return _custom_secret_value(secrets, name)


async def _validate_codex_credentials(
    request: AppConversationStartRequest,
    user_context: UserContext,
    secrets_store: SecretsStore,
) -> None:
    user = await user_context.get_user_info(
        resolve_agent_profile=True,
        override_agent_profile_id=request.agent_profile_id,
    )
    agent_settings = user.agent_settings
    if not (
        isinstance(agent_settings, ACPAgentSettings)
        and agent_settings.acp_server == 'codex'
    ):
        return

    secrets = await secrets_store.load()
    api_secrets = request.secrets or {}
    codex_auth = _request_or_stored_secret_value(
        secrets, api_secrets, 'CODEX_AUTH_JSON'
    )
    api_keys = (
        _request_or_stored_secret_value(secrets, api_secrets, 'OPENAI_API_KEY'),
        _request_or_stored_secret_value(secrets, api_secrets, 'CODEX_API_KEY'),
    )
    if is_valid_codex_auth(codex_auth) or any(map(_has_api_key, api_keys)):
        return

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            'Connect your Codex account or set an API key before starting a '
            'Codex conversation.'
        ),
    )


@dataclass
class AgentServerContext:
    """Context for accessing the agent server for a conversation."""

    conversation: AppConversationInfo
    sandbox: SandboxInfo
    sandbox_spec: SandboxSpecInfo
    agent_server_url: str
    session_api_key: str | None


async def _get_agent_server_context(
    conversation_id: UUID,
    app_conversation_service: AppConversationService,
    sandbox_service: SandboxService,
    sandbox_spec_service: SandboxSpecService,
) -> AgentServerContext | JSONResponse | None:
    """Get the agent server context for a conversation.

    This helper retrieves all necessary information to communicate with the
    agent server for a given conversation, including the sandbox info,
    sandbox spec, and agent server URL.

    Args:
        conversation_id: The conversation ID
        app_conversation_service: Service for conversation operations
        sandbox_service: Service for sandbox operations
        sandbox_spec_service: Service for sandbox spec operations

    Returns:
        AgentServerContext if successful, JSONResponse(404) if conversation
        not found, or None if sandbox is not running (e.g. closed conversation).
    """
    # Get the conversation info
    conversation = await app_conversation_service.get_app_conversation(conversation_id)
    if not conversation:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={'error': f'Conversation {conversation_id} not found'},
        )

    # Get the sandbox info
    sandbox = await sandbox_service.get_sandbox(conversation.sandbox_id)
    if not sandbox:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={'error': f'Sandbox not found for conversation {conversation_id}'},
        )
    # Return None for paused sandboxes (closed conversation)
    if sandbox.status == SandboxStatus.PAUSED:
        return None
    # Return 404 for other non-running states (STARTING, ERROR, MISSING)
    if sandbox.status != SandboxStatus.RUNNING:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={'error': f'Sandbox not ready for conversation {conversation_id}'},
        )

    # Get the sandbox spec to find the working directory
    sandbox_spec = await sandbox_spec_service.get_sandbox_spec(sandbox.sandbox_spec_id)
    if not sandbox_spec:
        # TODO: This is a temporary work around for the fact that we don't store previous
        # sandbox spec versions when updating OpenHands. When the SandboxSpecServices
        # transition to truly multi sandbox spec model this should raise a 404 error
        logger.warning('Sandbox spec not found - using default.')
        sandbox_spec = await sandbox_spec_service.get_default_sandbox_spec()

    # Get the agent server URL
    if not sandbox.exposed_urls:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={'error': 'No agent server URL found for sandbox'},
        )

    agent_server_url = None
    for exposed_url in sandbox.exposed_urls:
        if exposed_url.name == AGENT_SERVER:
            agent_server_url = exposed_url.url
            break

    if not agent_server_url:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={'error': 'Agent server URL not found in sandbox'},
        )

    agent_server_url = replace_localhost_hostname_for_docker(agent_server_url)

    return AgentServerContext(
        conversation=conversation,
        sandbox=sandbox,
        sandbox_spec=sandbox_spec,
        agent_server_url=agent_server_url,
        session_api_key=sandbox.session_api_key,
    )


# Read methods


@router.get('/search')
async def search_app_conversations(
    title__contains: Annotated[
        str | None,
        Query(title='Filter by title containing this string'),
    ] = None,
    created_at__gte: Annotated[
        datetime | None,
        Query(title='Filter by created_at greater than or equal to this datetime'),
    ] = None,
    created_at__lt: Annotated[
        datetime | None,
        Query(title='Filter by created_at less than this datetime'),
    ] = None,
    updated_at__gte: Annotated[
        datetime | None,
        Query(title='Filter by updated_at greater than or equal to this datetime'),
    ] = None,
    updated_at__lt: Annotated[
        datetime | None,
        Query(title='Filter by updated_at less than this datetime'),
    ] = None,
    sandbox_id__eq: Annotated[
        str | None,
        Query(title='Filter by exact sandbox_id'),
    ] = None,
    page_id: Annotated[
        str | None,
        Query(title='Optional next_page_id from the previously returned page'),
    ] = None,
    limit: Annotated[
        int,
        Query(
            title='The max number of results in the page',
            gt=0,
            le=100,
        ),
    ] = 100,
    include_sub_conversations: Annotated[
        bool,
        Query(
            title='If True, include sub-conversations in the results. If False (default), exclude all sub-conversations.'
        ),
    ] = False,
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
) -> AppConversationPage:
    """Search / List sandboxed conversations."""
    return await app_conversation_service.search_app_conversations(
        title__contains=title__contains,
        created_at__gte=created_at__gte,
        created_at__lt=created_at__lt,
        updated_at__gte=updated_at__gte,
        updated_at__lt=updated_at__lt,
        sandbox_id__eq=sandbox_id__eq,
        page_id=page_id,
        limit=limit,
        include_sub_conversations=include_sub_conversations,
    )


@router.get('/count')
async def count_app_conversations(
    title__contains: Annotated[
        str | None,
        Query(title='Filter by title containing this string'),
    ] = None,
    created_at__gte: Annotated[
        datetime | None,
        Query(title='Filter by created_at greater than or equal to this datetime'),
    ] = None,
    created_at__lt: Annotated[
        datetime | None,
        Query(title='Filter by created_at less than this datetime'),
    ] = None,
    updated_at__gte: Annotated[
        datetime | None,
        Query(title='Filter by updated_at greater than or equal to this datetime'),
    ] = None,
    updated_at__lt: Annotated[
        datetime | None,
        Query(title='Filter by updated_at less than this datetime'),
    ] = None,
    sandbox_id__eq: Annotated[
        str | None,
        Query(title='Filter by exact sandbox_id'),
    ] = None,
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
) -> int:
    """Count sandboxed conversations matching the given filters."""
    return await app_conversation_service.count_app_conversations(
        title__contains=title__contains,
        created_at__gte=created_at__gte,
        created_at__lt=created_at__lt,
        updated_at__gte=updated_at__gte,
        updated_at__lt=updated_at__lt,
        sandbox_id__eq=sandbox_id__eq,
    )


@router.get('')
async def batch_get_app_conversations(
    ids: Annotated[list[str], Query()],
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
) -> list[AppConversation | None]:
    """Get a batch of sandboxed conversations given their ids. Return None for any missing.

    Accepts UUIDs as strings (with or without dashes) and converts them internally.
    Returns 400 Bad Request if any string cannot be converted to a valid UUID.
    """
    if len(ids) >= 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Too many ids requested. Maximum is 99.',
        )

    uuids: list[UUID] = []
    invalid_ids: list[str] = []
    for id_str in ids:
        try:
            uuids.append(UUID(id_str))
        except ValueError:
            invalid_ids.append(id_str)

    if invalid_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Invalid UUID format for ids: {invalid_ids}',
        )

    app_conversations = await app_conversation_service.batch_get_app_conversations(
        uuids
    )
    return app_conversations


@router.post('')
async def start_app_conversation(
    request: Request,
    start_request: AppConversationStartRequest,
    user_context: UserContext = user_context_dependency,
    secrets_store: SecretsStore = Depends(get_secrets_store),
    db_session: AsyncSession = db_session_dependency,
    httpx_client: httpx.AsyncClient = httpx_client_dependency,
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
) -> AppConversationStartTask:
    await _validate_codex_credentials(start_request, user_context, secrets_store)

    # Because we are processing after the request finishes, keep the db connection open
    set_db_session_keep_open(request.state, True)
    set_httpx_client_keep_open(request.state, True)

    try:
        """Start an app conversation start task and return it."""
        async_iter = app_conversation_service.start_app_conversation(start_request)
        result = await anext(async_iter)

        # Analytics: conversation requested (V1)
        try:
            analytics = get_analytics_service()
            if analytics:
                user_id = await user_context.get_user_id()
                if user_id:
                    ctx = await resolve_analytics_context(user_id)
                    analytics.track_conversation_requested(
                        ctx=ctx,
                        request_id=result.id,
                        trigger=start_request.trigger.value
                        if start_request.trigger
                        else None,
                        agent_type='default',
                        has_repository=start_request.selected_repository is not None,
                        session_id=getattr(request.state, 'posthog_session_id', None),
                    )
        except Exception:
            logger.exception('analytics:conversation_requested:failed', stack_info=True)

        asyncio.create_task(_consume_remaining(async_iter, db_session, httpx_client))
        return result
    except Exception:
        await db_session.close()
        await httpx_client.aclose()
        raise


@router.patch('/{conversation_id}')
async def update_app_conversation(
    conversation_id: str,
    update_request: AppConversationUpdateRequest,
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
) -> AppConversation:
    info = await app_conversation_service.update_app_conversation(
        UUID(conversation_id), update_request
    )
    if info is None:
        raise HTTPException(404, 'unknown_app_conversation')
    return info


@router.post(
    '/{conversation_id}/send-message',
    responses={
        404: {'description': 'Conversation or sandbox not found'},
        409: {
            'description': 'Sandbox is not running. Resume it first via POST /sandboxes/{id}/resume'
        },
        410: {'description': 'Conversation is archived (sandbox no longer exists)'},
        503: {'description': 'Sandbox is in error state or agent server unavailable'},
    },
)
async def send_message_to_conversation(
    conversation_id: UUID,
    request: AppSendMessageRequest,
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
    sandbox_service: SandboxService = sandbox_service_dependency,
    httpx_client: httpx.AsyncClient = httpx_client_dependency,
) -> AppSendMessageResponse:
    """Send a follow-up message to an existing conversation.

    This REST endpoint provides a simplified way to send messages to a running
    conversation without requiring a WebSocket connection.

    **Alternative Approaches:**

    This endpoint is a convenience wrapper. You can also interact with the agent
    server directly using:

    1. **WebSocket**: Connect to the agent server's WebSocket endpoint for
       real-time bidirectional communication
    2. **Agent Server REST API**: Call the agent server's REST endpoints directly
       using the `conversation_url` from `GET /api/v1/app-conversations/{id}`

    **Design Note:**

    This endpoint is intentionally a thin proxy that forwards messages to the
    agent server without additional processing logic. Any custom processing
    (validation, transformation, side effects) should be implemented via
    webhook callbacks, not in this endpoint. This ensures that direct agent
    server invocation and this convenience endpoint remain functionally equivalent.

    **Prerequisites:**

    - The sandbox must be in RUNNING state
    - If the sandbox is PAUSED, call `POST /api/v1/sandboxes/{sandbox_id}/resume` first
    - If the sandbox is STARTING, wait for it to reach RUNNING state

    **Error responses:**

    - 404: Conversation or sandbox not found
    - 409: Sandbox exists but is not running (PAUSED, STARTING, STOPPING)
    - 410: Conversation is archived (sandbox no longer exists)
    - 503: Sandbox is in ERROR state or agent server is unavailable

    Args:
        conversation_id: The UUID of the conversation to send the message to
        request: The message content and options

    Returns:
        AppSendMessageResponse with success status and sandbox state
    """
    # Get conversation info
    conversation = await app_conversation_service.get_app_conversation(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Conversation {conversation_id} not found',
        )

    # Get sandbox info
    sandbox = await sandbox_service.get_sandbox(conversation.sandbox_id)
    if not sandbox:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Sandbox not found for conversation {conversation_id}',
        )

    # Check sandbox status - require RUNNING state
    if sandbox.status == SandboxStatus.MISSING:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail='Conversation is archived. The sandbox no longer exists.',
        )

    if sandbox.status == SandboxStatus.ERROR:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Sandbox is in an error state and cannot accept messages.',
        )

    if sandbox.status != SandboxStatus.RUNNING:
        # Sandbox exists but is not running (PAUSED, STARTING, STOPPING, etc.)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f'Sandbox is {sandbox.status.value}. '
                f'Use POST /api/v1/sandboxes/{sandbox.id}/resume to resume it first.'
            ),
        )

    # Get agent server URL from sandbox
    if not sandbox.exposed_urls:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='No agent server URL found for sandbox.',
        )

    agent_server_url = None
    for exposed_url in sandbox.exposed_urls:
        if exposed_url.name == AGENT_SERVER:
            agent_server_url = exposed_url.url
            break

    if not agent_server_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Agent server URL not found in sandbox.',
        )

    agent_server_url = replace_localhost_hostname_for_docker(agent_server_url)

    # Send message to agent server
    try:
        content_json = [item.model_dump() for item in request.content]
        response = await httpx_client.post(
            f'{agent_server_url}/api/conversations/{conversation_id}/events',
            json={
                'role': request.role,
                'content': content_json,
                'run': request.run,
            },
            headers=(
                {'X-Session-API-Key': sandbox.session_api_key}
                if sandbox.session_api_key
                else {}
            ),
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.exception(
            f'Agent server returned error when sending message: '
            f'{e.response.status_code} - {e.response.text}',
            stack_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'Agent server error: {e.response.status_code}',
        ) from e
    except httpx.RequestError as e:
        logger.exception('Failed to reach agent server', stack_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Failed to reach agent server.',
        ) from e

    return AppSendMessageResponse(
        success=True,
        sandbox_status=sandbox.status,
        message=None,
    )


async def _persist_conversation_model(
    app_conversation_info_service: AppConversationInfoService,
    conversation_id: UUID,
    model: str,
) -> None:
    """Persist ``llm_model`` on the conversation record so the UI chip/header
    reflects a model switch on the next fetch.

    Best-effort: a save failure is logged but never undoes the switch the
    agent-server already accepted.
    """
    try:
        info = await app_conversation_info_service.get_app_conversation_info(
            conversation_id,
        )
        if info is not None and info.llm_model != model:
            info.llm_model = model
            await app_conversation_info_service.save_app_conversation_info(info)
    except Exception:
        logger.exception(
            'Failed to persist new llm_model on conversation %s after model '
            'switch — chip may be stale until the next refresh.',
            conversation_id,
            stack_info=True,
        )


@router.post(
    '/{conversation_id}/switch_profile',
    responses={
        404: {'description': 'Conversation, sandbox, or profile not found'},
        409: {'description': 'Sandbox is not running'},
        502: {'description': 'Agent server returned an error'},
    },
)
async def switch_conversation_profile(
    conversation_id: UUID,
    request: SwitchProfileRequest,
    user_settings: Settings | None = Depends(get_user_settings),
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
    app_conversation_info_service: AppConversationInfoService = (
        app_conversation_info_service_dependency
    ),
    sandbox_service: SandboxService = sandbox_service_dependency,
    sandbox_spec_service: SandboxSpecService = sandbox_spec_service_dependency,
    httpx_client: httpx.AsyncClient = httpx_client_dependency,
) -> Success:
    """Switch the running conversation's LLM to a saved profile.

    Profiles live in the app-server's user settings, not on the sandbox FS,
    so we resolve the profile here and hand the LLM directly to the
    agent-server's ``switch_llm`` endpoint.
    """
    if user_settings is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Settings not found',
        )

    profile_llm = user_settings.llm_profiles.get(request.profile_name)
    if profile_llm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile '{request.profile_name}' not found",
        )

    # Resolve the saved profile for the agent server: provider-default base_url,
    # plus the effective settings key when the profile carries none (managed
    # profiles persist a masked key, so without this the agent server would hit
    # the litellm proxy unauthenticated). Locally, profiles carry their own key.
    settings_llm = getattr(user_settings.agent_settings, 'llm', None)
    profile_llm = resolve_profile_llm(
        profile_llm,
        managed_proxy_url=LITE_LLM_API_URL,
        fallback_api_key=getattr(settings_llm, 'api_key', None),
    )

    # The agent-server's LLM registry is first-write-wins by ``usage_id``:
    # ``switch_llm`` returns the cached entry under that key and silently
    # drops the incoming LLM. So:
    #   - Two saved profiles that share the default ``usage_id="default"``
    #     would no-op after the first switch (the second profile is dropped
    #     and the agent keeps using the first).
    #   - Editing a profile (e.g. swapping its model) wouldn't take effect
    #     on subsequent switches because the registry still holds the
    #     pre-edit LLM under the old slot.
    # Both manifest as "I switched profiles but the request still goes out
    # with the old model" (often surfacing as upstream "Invalid model name"
    # errors when the cached model has been removed from the user's quota).
    #
    # Derive ``usage_id`` from the profile name + a hash of the resolved
    # LLM payload. Identical snapshots dedupe in the registry; any change
    # (model, base_url, api_key, etc.) produces a fresh slot so the swap
    # actually lands.
    fingerprint = profile_llm.model_dump(
        mode='json',
        exclude={'usage_id'},
        exclude_none=True,
        context={'expose_secrets': True},
    )
    content_hash = hashlib.sha1(
        json.dumps(fingerprint, sort_keys=True, default=str).encode('utf-8'),
    ).hexdigest()[:12]
    profile_llm = profile_llm.model_copy(
        update={'usage_id': f'profile:{request.profile_name}:{content_hash}'},
    )

    ctx = await _get_agent_server_context(
        conversation_id,
        app_conversation_service,
        sandbox_service,
        sandbox_spec_service,
    )
    if isinstance(ctx, JSONResponse):
        # Helper already framed a 404 response; mirror its status code.
        raise HTTPException(
            status_code=ctx.status_code,
            detail=f'Conversation {conversation_id} is not reachable',
        )
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Sandbox is paused; resume it before switching profiles.',
        )

    llm_payload = profile_llm.model_dump(
        mode='json',
        exclude_none=True,
        context={'expose_secrets': True},
    )
    headers = {'X-Session-API-Key': ctx.session_api_key} if ctx.session_api_key else {}

    try:
        switch_response = await httpx_client.post(
            f'{ctx.agent_server_url}/api/conversations/{conversation_id}/switch_llm',
            json={'llm': llm_payload},
            headers=headers,
            timeout=30.0,
        )
        switch_response.raise_for_status()
        # Surface a success line so operators can confirm the swap landed
        # without grepping for the absence of an error. ``usage_id`` is the
        # registry key — different value across calls means the cache was
        # busted and a fresh LLM is in use; identical value means a cache
        # hit (intended for unchanged profiles).
        logger.info(
            'Switched conversation %s to profile %r '
            '(model=%s, base_url=%s, usage_id=%s)',
            conversation_id,
            request.profile_name,
            profile_llm.model,
            profile_llm.base_url,
            profile_llm.usage_id,
        )
    except httpx.HTTPStatusError as e:
        logger.exception(
            'Agent server returned error during switch_llm: '
            f'{e.response.status_code} - {e.response.text}',
            stack_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'Agent server error: {e.response.status_code}',
        ) from e
    except httpx.RequestError as e:
        logger.exception(
            'Failed to reach agent server during switch_llm', stack_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Failed to reach agent server.',
        ) from e

    # Persist the new model so the chat header reflects the swap on next fetch.
    await _persist_conversation_model(
        app_conversation_info_service, conversation_id, profile_llm.model
    )

    return Success()


@router.post(
    '/{conversation_id}/switch_acp_model',
    responses={
        400: {
            'description': 'Agent is not ACP, or provider does not support model switching'
        },
        404: {'description': 'Conversation or sandbox not found'},
        409: {'description': 'Sandbox is paused; resume it before switching models'},
        502: {'description': 'Agent server returned an error'},
        504: {'description': 'ACP server did not respond to the model switch in time'},
    },
)
async def switch_conversation_acp_model(
    conversation_id: UUID,
    request: SwitchAcpModelRequest,
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
    app_conversation_info_service: AppConversationInfoService = (
        app_conversation_info_service_dependency
    ),
    sandbox_service: SandboxService = sandbox_service_dependency,
    sandbox_spec_service: SandboxSpecService = sandbox_spec_service_dependency,
    httpx_client: httpx.AsyncClient = httpx_client_dependency,
) -> Success:
    """Switch the model of a running ACP conversation in place.

    Proxies to the agent-server's ``switch_acp_model`` endpoint, which issues
    a protocol-level ``session/set_model`` call to the ACP subprocess so the
    new model applies to subsequent turns without losing context. Persists the
    new model on the conversation record so the UI chip stays current.
    """
    ctx = await _get_agent_server_context(
        conversation_id,
        app_conversation_service,
        sandbox_service,
        sandbox_spec_service,
    )
    if isinstance(ctx, JSONResponse):
        raise HTTPException(
            status_code=ctx.status_code,
            detail=f'Conversation {conversation_id} is not reachable',
        )
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Sandbox is paused; resume it before switching models.',
        )

    headers = {'X-Session-API-Key': ctx.session_api_key} if ctx.session_api_key else {}

    try:
        switch_response = await httpx_client.post(
            f'{ctx.agent_server_url}/api/conversations/{conversation_id}/switch_acp_model',
            json={'model': request.model},
            headers=headers,
            timeout=30.0,
        )
        switch_response.raise_for_status()
        logger.info(
            'Switched ACP conversation %s to model %r',
            conversation_id,
            request.model,
        )
    except httpx.HTTPStatusError as e:
        logger.exception(
            'Agent server returned error during switch_acp_model: '
            f'{e.response.status_code} - {e.response.text}',
            stack_info=True,
        )
        # Surface agent-server's 400/504 directly (not-ACP, timeout). The
        # pre-session 409 band-aid is gone as of SDK #3764: a pre-run switch now
        # persists and returns 200, so the agent-server no longer 409s here.
        if e.response.status_code in (400, 504):
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f'Agent server error: {e.response.status_code}',
            ) from e
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'Agent server error: {e.response.status_code}',
        ) from e
    except httpx.RequestError as e:
        logger.exception(
            'Failed to reach agent server during switch_acp_model',
            stack_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Failed to reach agent server.',
        ) from e

    # Persist so the conversation's model chip reflects the switch on next load.
    await _persist_conversation_model(
        app_conversation_info_service, conversation_id, request.model
    )

    return Success()


async def _finalize_sandbox_delete(
    sandbox_service: SandboxService,
    app_conversation_info_service: AppConversationInfoService,
    sandbox_id: str,
    db_session: AsyncSession,
    httpx_client: httpx.AsyncClient,
    conversation_id: UUID | None = None,
    workspace_path: str | None = None,
) -> None:
    """Archive the conversation's workspace, then delete the sandbox if unreferenced.

    Runs detached (background task) AFTER the delete response was already returned.
    The workspace is captured FIRST — a conversation-scoped step, while the runtime
    is still up — and only if that succeeds (or archiving is not REQUIRED) is the
    sandbox torn down, and only when no other conversation still references it
    (under grouping a sibling conversation keeps it alive). When archiving is
    REQUIRED and fails, the sandbox + running runtime are kept so the runtime-api
    idle reap captures the workspace later (the durability backstop). delete_sandbox
    is sandbox-scoped (stop + delete) and knows nothing about conversations.
    """
    try:
        archived = await sandbox_service.archive_conversation_workspace(
            sandbox_id,
            conversation_id=conversation_id.hex if conversation_id else None,
            workspace_path=workspace_path,
        )
        if not archived:
            # REQUIRED archive failed: keep the sandbox + running runtime for the
            # runtime-api idle reap to capture (the durability backstop).
            logger.warning(
                'Workspace archive required but failed for %s; leaving the '
                'sandbox + runtime for the idle reap',
                sandbox_id,
            )
        else:
            conversation_count = (
                await app_conversation_info_service.count_conversations_by_sandbox_id(
                    sandbox_id
                )
            )
            if conversation_count == 0:
                await sandbox_service.delete_sandbox(sandbox_id)
        await db_session.commit()
    except Exception:
        # Any failure in the finalizer (a transient stop/lookup error, the count
        # query, the commit itself): do NOT commit a half-done delete, so no
        # orphaned row is left; the row + running runtime stay for the runtime-api
        # idle reap to capture + reap.
        logger.exception(
            'Deferred sandbox cleanup failed for %s; kept for retry',
            sandbox_id,
            stack_info=True,
        )
        await db_session.rollback()
    finally:
        await asyncio.gather(
            db_session.aclose(),
            httpx_client.aclose(),
        )


@router.delete('/{conversation_id}', responses={404: {'description': 'Item not found'}})
async def delete_app_conversation(
    request: Request,
    conversation_id: str,
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
    app_conversation_info_service: AppConversationInfoService = (
        app_conversation_info_service_dependency
    ),
    sandbox_service: SandboxService = sandbox_service_dependency,
    db_session: AsyncSession = db_session_dependency,
    httpx_client: httpx.AsyncClient = httpx_client_dependency,
) -> Success:
    """Delete an app conversation and its associated data.

    This endpoint deletes the conversation and cleans up sandbox resources
    if no other conversations are using the same sandbox.
    """
    try:
        conversation_uuid = UUID(conversation_id)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, 'Invalid conversation ID format'
        )

    # Get conversation info to check if it exists and get sandbox_id
    app_conversation_info = (
        await app_conversation_info_service.get_app_conversation_info(conversation_uuid)
    )
    if not app_conversation_info:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Conversation not found')

    sandbox_id = app_conversation_info.sandbox_id

    # Check if sandbox is shared with other conversations
    sandbox_is_shared = False
    if sandbox_id:
        conversation_count = (
            await app_conversation_info_service.count_conversations_by_sandbox_id(
                sandbox_id
            )
        )
        sandbox_is_shared = conversation_count > 1

    # Delete the conversation (skip agent server DELETE if sandbox is shared)
    deleted = await app_conversation_service.delete_app_conversation(
        conversation_uuid,
        skip_agent_server_delete=sandbox_is_shared,
    )
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Failed to delete conversation')

    # Analytics: conversation deleted (V1)
    try:
        analytics = get_analytics_service()
        if analytics and app_conversation_info.created_by_user_id:
            ctx = await resolve_analytics_context(
                app_conversation_info.created_by_user_id
            )
            analytics.track_conversation_deleted(
                ctx=ctx,
                conversation_id=conversation_id,
            )
    except Exception:
        logger.exception('analytics:conversation_deleted:failed', stack_info=True)

    # Commit the deletion
    await db_session.commit()

    # Keep connections open for background task
    set_db_session_keep_open(request.state, True)
    set_httpx_client_keep_open(request.state, True)

    # Delete the sandbox in the background if no other conversations reference it
    if sandbox_id:
        # Path pinned at creation; the finalizer archives exactly this directory.
        workspace_path = app_conversation_info.tags.get(ARCHIVE_WORKSPACE_PATH_TAG_KEY)
        asyncio.create_task(
            _finalize_sandbox_delete(
                sandbox_service,
                app_conversation_info_service,
                sandbox_id,
                db_session,
                httpx_client,
                conversation_id=conversation_uuid,
                workspace_path=workspace_path,
            )
        )

    return Success()


@router.post('/stream-start')
async def stream_app_conversation_start(
    request: AppConversationStartRequest,
    user_context: UserContext = user_context_dependency,
    secrets_store: SecretsStore = Depends(get_secrets_store),
) -> list[AppConversationStartTask]:
    """Start an app conversation start task and stream updates from it.
    Leaves the connection open until either the conversation starts or there was an error
    """
    await _validate_codex_credentials(request, user_context, secrets_store)
    response = StreamingResponse(
        _stream_app_conversation_start(request, user_context),
        media_type='application/json',
    )
    return response


@router.get('/start-tasks/search')
async def search_app_conversation_start_tasks(
    conversation_id__eq: Annotated[
        UUID | None,
        Query(title='Filter by conversation ID equal to this value'),
    ] = None,
    created_at__gte: Annotated[
        datetime | None,
        Query(title='Filter by created_at greater than or equal to this datetime'),
    ] = None,
    sort_order: Annotated[
        AppConversationStartTaskSortOrder,
        Query(title='Sort order for the results'),
    ] = AppConversationStartTaskSortOrder.CREATED_AT_DESC,
    page_id: Annotated[
        str | None,
        Query(title='Optional next_page_id from the previously returned page'),
    ] = None,
    limit: Annotated[
        int,
        Query(
            title='The max number of results in the page',
            gt=0,
            le=100,
        ),
    ] = 100,
    app_conversation_start_task_service: AppConversationStartTaskService = (
        app_conversation_start_task_service_dependency
    ),
) -> AppConversationStartTaskPage:
    """Search / List conversation start tasks."""
    return (
        await app_conversation_start_task_service.search_app_conversation_start_tasks(
            conversation_id__eq=conversation_id__eq,
            created_at__gte=created_at__gte,
            sort_order=sort_order,
            page_id=page_id,
            limit=limit,
        )
    )


@router.get('/start-tasks/count')
async def count_app_conversation_start_tasks(
    conversation_id__eq: Annotated[
        UUID | None,
        Query(title='Filter by conversation ID equal to this value'),
    ] = None,
    created_at__gte: Annotated[
        datetime | None,
        Query(title='Filter by created_at greater than or equal to this datetime'),
    ] = None,
    app_conversation_start_task_service: AppConversationStartTaskService = (
        app_conversation_start_task_service_dependency
    ),
) -> int:
    """Count conversation start tasks matching the given filters."""
    return await app_conversation_start_task_service.count_app_conversation_start_tasks(
        conversation_id__eq=conversation_id__eq,
        created_at__gte=created_at__gte,
    )


@router.get('/start-tasks')
async def batch_get_app_conversation_start_tasks(
    ids: Annotated[list[UUID], Query()],
    app_conversation_start_task_service: AppConversationStartTaskService = (
        app_conversation_start_task_service_dependency
    ),
) -> list[AppConversationStartTask | None]:
    """Get a batch of start app conversation tasks given their ids. Return None for any missing."""
    if len(ids) > 100:
        raise HTTPException(
            status_code=400,
            detail=f'Cannot request more than 100 start tasks at once, got {len(ids)}',
        )
    start_tasks = await app_conversation_start_task_service.batch_get_app_conversation_start_tasks(
        ids
    )
    return start_tasks


@router.get('/{conversation_id}/file')
async def read_conversation_file(
    conversation_id: UUID,
    file_path: Annotated[
        str,
        Query(title='Path to the file to read within the sandbox workspace'),
    ] = '/workspace/project/PLAN.md',
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
    sandbox_service: SandboxService = sandbox_service_dependency,
    sandbox_spec_service: SandboxSpecService = sandbox_spec_service_dependency,
) -> str:
    """Read a file from a specific conversation's sandbox workspace.

    Returns the content of the file at the specified path if it exists, otherwise returns an empty string.

    Args:
        conversation_id: The UUID of the conversation
        file_path: Path to the file to read within the sandbox workspace

    Returns:
        The content of the file or an empty string if the file doesn't exist
    """
    # Get the conversation info
    conversation = await app_conversation_service.get_app_conversation(conversation_id)
    if not conversation:
        return ''

    # Get the sandbox info
    sandbox = await sandbox_service.get_sandbox(conversation.sandbox_id)
    if not sandbox or sandbox.status != SandboxStatus.RUNNING:
        return ''

    # Get the sandbox spec to find the working directory
    sandbox_spec = await sandbox_spec_service.get_sandbox_spec(sandbox.sandbox_spec_id)
    if not sandbox_spec:
        return ''

    # Get the agent server URL
    if not sandbox.exposed_urls:
        return ''

    agent_server_url = None
    for exposed_url in sandbox.exposed_urls:
        if exposed_url.name == AGENT_SERVER:
            agent_server_url = exposed_url.url
            break

    if not agent_server_url:
        return ''

    agent_server_url = replace_localhost_hostname_for_docker(agent_server_url)

    # Create remote workspace
    remote_workspace = AsyncRemoteWorkspace(
        host=agent_server_url,
        api_key=sandbox.session_api_key,
        working_dir=sandbox_spec.working_dir,
    )

    # The runtime's file_download requires an absolute path that already points
    # at the file. The frontend may have rooted ``file_path`` at its own
    # working-dir convention (e.g. ``/workspace/project/enterprise/...``)
    # rather than the runtime's actual clone location
    # (``{working_dir}/{repo_name}/...``); remap it onto the resolved project
    # dir so the download resolves the right file instead of 404-ing into "".
    ctx = AgentServerContext(
        conversation=conversation,
        sandbox=sandbox,
        sandbox_spec=sandbox_spec,
        agent_server_url=agent_server_url,
        session_api_key=sandbox.session_api_key,
    )
    resolved_path = _resolve_file_path(file_path, ctx)

    # Read the file at the specified path
    temp_file_path = None
    try:
        # Create a temporary file path to download the remote file
        with tempfile.NamedTemporaryFile(mode='w+b', delete=False) as temp_file:
            temp_file_path = temp_file.name

        # Download the file from remote system
        result = await remote_workspace.file_download(
            source_path=resolved_path,
            destination_path=temp_file_path,
        )

        if result.success:
            # Read the content from the temporary file
            with open(temp_file_path, 'rb') as f:
                content = f.read()
            # Decode bytes to string
            return content.decode('utf-8')
    except Exception:
        # If there's any error reading the file, return empty string
        pass
    finally:
        # Clean up the temporary file
        if temp_file_path:
            try:
                os.unlink(temp_file_path)
            except Exception:
                # Ignore errors during cleanup
                pass

    return ''


async def _proxy_git_runtime_call(
    conversation_id: UUID,
    runtime_path: str,
    path: str,
    ref: str | None,
    app_conversation_service: AppConversationService,
    sandbox_service: SandboxService,
    sandbox_spec_service: SandboxSpecService,
    httpx_client: httpx.AsyncClient,
) -> Any:
    """Resolve the conversation's runtime and proxy a GET to ``runtime_path``.

    Browsers can't reach runtime sandboxes directly (no CORS for non-localhost
    origins on most paths), so the frontend hits these endpoints on the cloud
    API host instead and we make the runtime hop server-side using the
    sandbox's session API key.
    """
    ctx = await _get_agent_server_context(
        conversation_id,
        app_conversation_service,
        sandbox_service,
        sandbox_spec_service,
    )
    if isinstance(ctx, JSONResponse):
        raise HTTPException(
            status_code=ctx.status_code,
            detail=f'Conversation {conversation_id} is not reachable',
        )
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Sandbox is paused; resume it before reading git state.',
        )

    headers = {'X-Session-API-Key': ctx.session_api_key} if ctx.session_api_key else {}
    params: dict[str, str] = {'path': path}
    if ref is not None:
        params['ref'] = ref

    try:
        upstream = await httpx_client.get(
            f'{ctx.agent_server_url}{runtime_path}',
            params=params,
            headers=headers,
            timeout=30.0,
        )
        upstream.raise_for_status()
        return upstream.json()
    except httpx.HTTPStatusError as e:
        logger.exception(
            'Agent server returned error during %s: %s - %s',
            runtime_path,
            e.response.status_code,
            e.response.text,
            stack_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'Agent server error: {e.response.status_code}',
        ) from e
    except (json.JSONDecodeError, httpx.DecodingError) as e:
        logger.exception(
            'Agent server returned non-JSON during %s: %s',
            runtime_path,
            e,
            stack_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Agent server returned unexpected response.',
        ) from e
    except httpx.RequestError as e:
        logger.exception(
            'Failed to reach agent server during %s: %s',
            runtime_path,
            e,
            stack_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Failed to reach agent server.',
        ) from e


@router.get('/{conversation_id}/git/changes')
async def get_conversation_git_changes(
    conversation_id: UUID,
    path: Annotated[
        str,
        Query(
            description=(
                'Absolute path to the git repository root (e.g. /workspace/project)'
            ),
        ),
    ],
    ref: Annotated[
        str | None, Query(description='Optional git ref to diff against')
    ] = None,
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
    sandbox_service: SandboxService = sandbox_service_dependency,
    sandbox_spec_service: SandboxSpecService = sandbox_spec_service_dependency,
    httpx_client: httpx.AsyncClient = httpx_client_dependency,
) -> Any:
    """Proxy ``GET /api/git/changes`` on the conversation's runtime."""
    return await _proxy_git_runtime_call(
        conversation_id,
        '/api/git/changes',
        path,
        ref,
        app_conversation_service,
        sandbox_service,
        sandbox_spec_service,
        httpx_client,
    )


@router.get('/{conversation_id}/git/diff')
async def get_conversation_git_diff(
    conversation_id: UUID,
    path: Annotated[str, Query(description='The file path to diff')],
    ref: Annotated[
        str | None, Query(description='Optional git ref to diff against')
    ] = None,
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
    sandbox_service: SandboxService = sandbox_service_dependency,
    sandbox_spec_service: SandboxSpecService = sandbox_spec_service_dependency,
    httpx_client: httpx.AsyncClient = httpx_client_dependency,
) -> Any:
    """Proxy ``GET /api/git/diff`` on the conversation's runtime."""
    return await _proxy_git_runtime_call(
        conversation_id,
        '/api/git/diff',
        path,
        ref,
        app_conversation_service,
        sandbox_service,
        sandbox_spec_service,
        httpx_client,
    )


# Directory names we never descend into when listing workspace files — kept in
# sync with the Agent Canvas frontend's local `find` listing so cloud and local
# backends exclude the same heavy/build dirs.
_WORKSPACE_LIST_EXCLUDED_DIRS = (
    '.git',
    'node_modules',
    '.venv',
    'venv',
    '__pycache__',
    'dist',
    'build',
    '.next',
    '.cache',
    '.pytest_cache',
    '.mypy_cache',
    '.turbo',
    '.parcel-cache',
    'target',
)

# Cap the number of files returned so a giant repo doesn't overwhelm the UI.
_WORKSPACE_LIST_MAX_FILES = 2000


def _build_workspace_list_command() -> str:
    """`find` invocation that lists regular files relative to the cwd,
    pruning heavy/build directories and bounding the result."""
    prune_expr = ' -o '.join(
        f"-name '{name}' -prune" for name in _WORKSPACE_LIST_EXCLUDED_DIRS
    )
    return (
        f'find . \\( {prune_expr} \\) -o -type f -print 2>/dev/null '
        f'| sort | head -n {_WORKSPACE_LIST_MAX_FILES}'
    )


def _resolve_workspace_dir(path: str | None, ctx: AgentServerContext) -> str:
    """Resolve the directory to list files in.

    The caller may pass ``path`` (e.g. the value the frontend computed from its
    working-dir convention), but that convention does not always match the
    runtime's actual working directory — different deployments clone the repo
    at ``{working_dir}/{repo_name}``, ``{working_dir}/{conversation_id}``, or
    plain ``working_dir``. The authoritative project root is derived from the
    sandbox spec's ``working_dir`` and the conversation's
    ``selected_repository`` (the same resolution the skills/hooks endpoints
    use), so prefer it. ``path`` is only honored when it points at a real
    subdirectory the runtime exposes — i.e. it is the resolved project dir or a
    descendant of it — which keeps "list a subdirectory of the workspace"
    working without ever listing outside the workspace.
    """
    project_dir = get_project_dir(
        ctx.sandbox_spec.working_dir, ctx.conversation.selected_repository
    )
    if not path:
        return project_dir
    # Accept the caller's path only when it is the resolved project dir or a
    # descendant of it, so "list a subdirectory of the workspace" still works
    # without ever listing outside the conversation's project. The bare
    # ``working_dir`` is deliberately NOT a candidate when a repository is
    # selected: it is a *parent* of the clone, so treating it as an anchor
    # would accept any sibling path (e.g. ``/workspace/project`` under
    # ``/workspace``) and list the wrong — often nonexistent — directory.
    base = project_dir.rstrip('/')
    normalized = path.rstrip('/')
    if normalized == base or normalized.startswith(base.rstrip('/') + '/'):
        return path
    return project_dir


def _resolve_file_path(file_path: str, ctx: AgentServerContext) -> str:
    """Resolve a file path to an absolute path inside the conversation's project.

    The runtime's ``/api/file/download`` requires an **absolute** path and does
    not join it with the workspace's ``working_dir``, so the path we hand it
    must already point at the right place. The frontend builds its request path
    as ``{workspaceRoot}/{relativePath}``, where ``relativePath`` is correct
    (it comes from the ``/files`` tree, which is relative to the real project
    dir) but ``workspaceRoot`` is the frontend's working-dir convention. When
    the cloud conversation response does not expose ``workspace.working_dir``
    that convention falls back to ``{DEFAULT_WORKING_DIR}[/{repoName}]`` (e.g.
    ``/workspace/project/enterprise``), which does not match the runtime's
    actual clone location (``{working_dir}/{repo_name}``, e.g.
    ``/workspace/enterprise``). The download then 404s and the endpoint
    silently returns ``""``.

    This remaps the path onto the authoritative project dir derived from the
    sandbox spec (the same ``get_project_dir`` resolution used by
    ``_resolve_workspace_dir`` and the skills/hooks endpoints):

    * an absolute path already inside the project dir is used as-is;
    * an absolute path rooted at a stale frontend default is re-anchored by
      stripping the stale root prefix (longest match first, so
      ``/workspace/project/enterprise`` is preferred over ``/workspace``) and
      re-joining under the project dir;
    * a relative path is joined under the project dir (the runtime rejects
      relative paths, so this also fixes that case).
    """
    working_dir = ctx.sandbox_spec.working_dir
    repo_name = (
        ctx.conversation.selected_repository.split('/')[-1]
        if ctx.conversation.selected_repository
        else None
    )
    project_dir = get_project_dir(working_dir, ctx.conversation.selected_repository)

    if not file_path:
        return project_dir

    normalized = file_path.rstrip('/')
    if not normalized.startswith('/'):
        # Relative path: anchor under the project dir.
        return f'{project_dir}/{normalized}'

    project_base = project_dir.rstrip('/')
    if normalized == project_base or normalized.startswith(project_base + '/'):
        # Already inside the real project dir.
        return file_path

    # The path is absolute but not under the project dir. It is likely rooted
    # at one of the frontend's stale defaults; strip the longest matching
    # stale root and re-anchor under the project dir. Ordered longest-first so
    # a more specific root (``/workspace/project/enterprise``) wins over its
    # parent (``/workspace/project``), and the bare ``working_dir`` (which is
    # a parent of the clone) is only a last resort.
    stale_roots: list[str] = []
    if repo_name:
        stale_roots.append(f'{working_dir}/project/{repo_name}')
    stale_roots.append(f'{working_dir}/project')
    stale_roots.append(working_dir)
    for root in sorted({r.rstrip('/') for r in stale_roots}, key=len, reverse=True):
        if normalized == root or normalized.startswith(root + '/'):
            remainder = normalized[len(root) :].lstrip('/')
            return f'{project_dir}/{remainder}' if remainder else project_dir

    # No recognizable root; let the runtime reject it (returns "").
    return file_path


@router.get('/{conversation_id}/files')
async def list_conversation_files(
    conversation_id: UUID,
    path: Annotated[
        str | None,
        Query(
            description=(
                'Optional absolute path to the workspace directory to list. '
                'When omitted, or when it does not match the conversation '
                'workspace, the directory is resolved from the sandbox spec '
                "and the conversation's selected repository."
            ),
        ),
    ] = None,
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
    sandbox_service: SandboxService = sandbox_service_dependency,
    sandbox_spec_service: SandboxSpecService = sandbox_spec_service_dependency,
    httpx_client: httpx.AsyncClient = httpx_client_dependency,
) -> list[str]:
    """List every regular file under a conversation workspace directory.

    Mirrors the git-proxy endpoints: browsers can't reach runtime sandboxes
    directly (no CORS for non-localhost origins), so the frontend hits this on
    the cloud API host and we make the runtime hop server-side using the
    sandbox's session API key. Unlike `/git/changes` (which only reports
    modified/untracked files), this enumerates the full tree so the Files tab
    matches the local-backend experience. Paths are returned relative to the
    listed directory (e.g. ``src/index.html``).
    """
    ctx = await _get_agent_server_context(
        conversation_id,
        app_conversation_service,
        sandbox_service,
        sandbox_spec_service,
    )
    if isinstance(ctx, JSONResponse):
        raise HTTPException(
            status_code=ctx.status_code,
            detail=f'Conversation {conversation_id} is not reachable',
        )
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Sandbox is paused; resume it before listing files.',
        )

    cwd = _resolve_workspace_dir(path, ctx)
    headers = {'X-Session-API-Key': ctx.session_api_key} if ctx.session_api_key else {}
    try:
        upstream = await httpx_client.post(
            f'{ctx.agent_server_url}/api/bash/execute_bash_command',
            json={
                'command': _build_workspace_list_command(),
                'cwd': cwd,
                'timeout': 30,
            },
            headers=headers,
            timeout=40.0,
        )
        upstream.raise_for_status()
        data = upstream.json()
    except httpx.HTTPStatusError as e:
        logger.exception(
            'Agent server returned error listing files: %s - %s',
            e.response.status_code,
            e.response.text,
            stack_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'Agent server error: {e.response.status_code}',
        ) from e
    except (json.JSONDecodeError, httpx.DecodingError) as e:
        logger.exception(
            'Agent server returned non-JSON listing files: %s', e, stack_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Agent server returned unexpected response.',
        ) from e
    except httpx.RequestError as e:
        logger.exception(
            'Failed to reach agent server listing files: %s', e, stack_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Failed to reach agent server.',
        ) from e

    if not isinstance(data, dict) or data.get('exit_code') != 0:
        # A non-zero exit (e.g. the directory doesn't exist yet) means there is
        # nothing to list rather than an error worth surfacing to the UI.
        return []

    stdout = data.get('stdout') or ''
    seen: set[str] = set()
    files: list[str] = []
    for line in stdout.splitlines():
        rel = line.strip()
        if rel.startswith('./'):
            rel = rel[2:]
        if not rel or rel in seen:
            continue
        seen.add(rel)
        files.append(rel)
        if len(files) >= _WORKSPACE_LIST_MAX_FILES:
            break
    return files


@router.get('/{conversation_id}/skills')
async def get_conversation_skills(
    conversation_id: UUID,
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
    sandbox_service: SandboxService = sandbox_service_dependency,
    sandbox_spec_service: SandboxSpecService = sandbox_spec_service_dependency,
) -> JSONResponse:
    """Get all skills associated with the conversation.

    This endpoint returns all skills that are loaded for the v1 conversation.
    Skills are loaded from multiple sources:
    - Sandbox skills (exposed URLs)
    - Global skills (OpenHands/skills/)
    - User skills (~/.openhands/skills/)
    - Organization skills (org/.openhands repository)
    - Repository skills (repo .agents/skills/, .openhands/microagents/, and legacy .openhands/skills/)
    - Registered marketplaces (auto-load plugins from instance/org/user settings)

    Returns:
        JSONResponse: A JSON response containing the list of skills.
        Returns an empty list if the sandbox is not running.
    """
    try:
        # Get agent server context (conversation, sandbox, sandbox_spec, agent_server_url)
        ctx = await _get_agent_server_context(
            conversation_id,
            app_conversation_service,
            sandbox_service,
            sandbox_spec_service,
        )
        if isinstance(ctx, JSONResponse):
            return ctx
        if ctx is None:
            return JSONResponse(status_code=status.HTTP_200_OK, content={'skills': []})

        # Load skills from all sources
        logger.info(f'Loading skills for conversation {conversation_id}')

        # Prefer the shared loader to avoid duplication; otherwise return empty list.
        all_skills: list = []
        if isinstance(app_conversation_service, AppConversationServiceBase):
            project_dir = get_project_dir(
                ctx.sandbox_spec.working_dir, ctx.conversation.selected_repository
            )
            # Same marketplaces conversation start hands to the agent-server, so
            # the listing includes auto-loaded marketplace plugin skills.
            user = await app_conversation_service.user_context.get_user_info()
            registered_marketplaces = await resolve_registered_marketplaces(
                app_conversation_service.user_context, user
            )
            all_skills = await app_conversation_service.load_and_merge_all_skills(
                ctx.sandbox,
                ctx.conversation.selected_repository,
                project_dir,
                ctx.agent_server_url,
                registered_marketplaces=registered_marketplaces,
            )

        logger.info(
            f'Loaded {len(all_skills)} skills for conversation {conversation_id}: '
            f'{[s.name for s in all_skills]}'
        )

        # Transform skills to response format
        skills_response = []
        for skill in all_skills:
            # Determine type based on AgentSkills format and trigger
            skill_type: Literal['repo', 'knowledge', 'agentskills']
            if skill.is_agentskills_format:
                skill_type = 'agentskills'
            elif skill.trigger is None:
                skill_type = 'repo'
            else:
                skill_type = 'knowledge'

            # Extract triggers
            triggers: list[str] = []
            if isinstance(skill.trigger, (KeywordTrigger, TaskTrigger)):
                if hasattr(skill.trigger, 'keywords'):
                    triggers = skill.trigger.keywords
                elif hasattr(skill.trigger, 'triggers'):
                    triggers = skill.trigger.triggers

            skills_response.append(
                SkillResponse(
                    name=skill.name,
                    type=skill_type,
                    content=skill.content,
                    triggers=triggers,
                )
            )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={'skills': [s.model_dump() for s in skills_response]},
        )

    except Exception as e:
        logger.exception(
            f'Error getting skills for conversation {conversation_id}',
            stack_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={'error': f'Error getting skills: {str(e)}'},
        )


@router.get('/{conversation_id}/hooks')
async def get_conversation_hooks(
    conversation_id: UUID,
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
    sandbox_service: SandboxService = sandbox_service_dependency,
    sandbox_spec_service: SandboxSpecService = sandbox_spec_service_dependency,
    httpx_client: httpx.AsyncClient = httpx_client_dependency,
) -> JSONResponse:
    """Get hooks currently configured in the workspace for this conversation.

    This endpoint loads hooks from the conversation's project directory in the
    workspace (i.e. `{project_dir}/.openhands/hooks.json`) at request time.

    Note:
        This is intentionally a "live" view of the workspace configuration.
        If `.openhands/hooks.json` changes over time, this endpoint reflects the
        latest file content and may not match the hooks that were used when the
        conversation originally started.

    Returns:
        JSONResponse: A JSON response containing the list of hook event types.
        Returns an empty list if the sandbox is not running.
    """
    try:
        # Get agent server context (conversation, sandbox, sandbox_spec, agent_server_url)
        ctx = await _get_agent_server_context(
            conversation_id,
            app_conversation_service,
            sandbox_service,
            sandbox_spec_service,
        )
        if isinstance(ctx, JSONResponse):
            return ctx
        if ctx is None:
            return JSONResponse(status_code=status.HTTP_200_OK, content={'hooks': []})

        from openhands.app_server.app_conversation.hook_loader import (
            fetch_hooks_from_agent_server,
            get_project_dir_for_hooks,
        )

        project_dir = get_project_dir_for_hooks(
            ctx.sandbox_spec.working_dir,
            ctx.conversation.selected_repository,
        )

        # Load hooks from agent-server (using the error-raising variant so
        # HTTP/connection failures are surfaced to the user, not hidden).
        logger.debug(
            f'Loading hooks for conversation {conversation_id}, '
            f'agent_server_url={ctx.agent_server_url}, '
            f'project_dir={project_dir}'
        )

        try:
            hook_config = await fetch_hooks_from_agent_server(
                agent_server_url=ctx.agent_server_url,
                session_api_key=ctx.session_api_key,
                project_dir=project_dir,
                httpx_client=httpx_client,
            )
        except httpx.HTTPStatusError as e:
            logger.warning(
                f'Agent-server returned {e.response.status_code} when loading hooks '
                f'for conversation {conversation_id}: {e.response.text}'
            )
            return JSONResponse(
                status_code=status.HTTP_502_BAD_GATEWAY,
                content={
                    'error': f'Agent-server returned status {e.response.status_code} when loading hooks'
                },
            )
        except httpx.RequestError as e:
            logger.warning(
                f'Failed to reach agent-server when loading hooks '
                f'for conversation {conversation_id}: {e}'
            )
            return JSONResponse(
                status_code=status.HTTP_502_BAD_GATEWAY,
                content={'error': 'Failed to reach agent-server when loading hooks'},
            )

        # Transform hook_config to response format
        hooks_response: list[HookEventResponse] = []

        if hook_config:
            # Define the event types to check
            event_types = [
                'pre_tool_use',
                'post_tool_use',
                'user_prompt_submit',
                'session_start',
                'session_end',
                'stop',
            ]

            for field_name in event_types:
                matchers = getattr(hook_config, field_name, [])
                if matchers:
                    matcher_responses = []
                    for matcher in matchers:
                        hook_defs = [
                            HookDefinitionResponse(
                                type=hook.type.value
                                if hasattr(hook.type, 'value')
                                else str(hook.type),
                                command=hook.command,
                                timeout=hook.timeout,
                                async_=hook.async_,
                            )
                            for hook in matcher.hooks
                        ]
                        matcher_responses.append(
                            HookMatcherResponse(
                                matcher=matcher.matcher,
                                hooks=hook_defs,
                            )
                        )
                    hooks_response.append(
                        HookEventResponse(
                            event_type=field_name,
                            matchers=matcher_responses,
                        )
                    )

        logger.debug(
            f'Loaded {len(hooks_response)} hook event types for conversation {conversation_id}'
        )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=GetHooksResponse(hooks=hooks_response).model_dump(by_alias=True),
        )

    except Exception as e:
        logger.exception(
            f'Error getting hooks for conversation {conversation_id}',
            stack_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={'error': f'Error getting hooks: {str(e)}'},
        )


@router.get('/{conversation_id}/download')
async def export_conversation(
    conversation_id: UUID,
    app_conversation_service: AppConversationService = (
        app_conversation_service_dependency
    ),
    user_context: UserContext = user_context_dependency,
):
    """Download a conversation trajectory as a zip file.

    Returns a zip file containing all events and metadata for the conversation.

    Args:
        conversation_id: The UUID of the conversation to download

    Returns:
        A zip file containing the conversation trajectory
    """
    try:
        # Prepare the zip stream before sending headers so lock and validation
        # errors can still be returned as HTTP status codes.
        zip_stream = await app_conversation_service.open_conversation_export(
            conversation_id
        )

        # Analytics: track trajectory download
        try:
            analytics = get_analytics_service()
            user_id = await user_context.get_user_id()
            if analytics and user_id:
                from openhands.analytics.analytics_context import AnalyticsContext

                user_info = await user_context.get_user_info()
                ctx = AnalyticsContext(
                    user_id=user_id,
                    consented=user_info.user_consents_to_analytics
                    if user_info and user_info.user_consents_to_analytics is not None
                    else False,
                    org_id=None,
                    user=None,
                )
                analytics.track_trajectory_downloaded(
                    ctx=ctx,
                    conversation_id=str(conversation_id),
                )
        except Exception:
            logger.exception('analytics:trajectory_downloaded:failed', stack_info=True)

        return StreamingResponse(
            zip_stream,
            media_type='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename="conversation_{conversation_id}.zip"'
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ConversationExportAlreadyRunning as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ConversationExportLockUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ConversationExportTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f'Failed to download trajectory: {str(e)}'
        ) from e


async def _consume_remaining(
    async_iter, db_session: AsyncSession, httpx_client: httpx.AsyncClient
):
    """Consume the remaining items from an async iterator"""
    try:
        while True:
            await anext(async_iter)
    except StopAsyncIteration:
        return
    finally:
        await db_session.close()
        await httpx_client.aclose()


async def _stream_app_conversation_start(
    request: AppConversationStartRequest,
    user_context: UserContext,
) -> AsyncGenerator[str, None]:
    """Stream a json list, item by item."""
    # Because the original dependencies are closed after the method returns, we need
    # a new dependency context which will continue intil the stream finishes.
    state = InjectorState()
    setattr(state, USER_CONTEXT_ATTR, user_context)
    async with get_app_conversation_service(state) as app_conversation_service:
        yield '[\n'
        comma = False
        async for task in app_conversation_service.start_app_conversation(request):
            chunk = task.model_dump_json()
            if comma:
                chunk = ',\n' + chunk
            comma = True
            yield chunk
        yield ']'
