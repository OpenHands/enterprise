"""Event Callback router for OpenHands App Server."""

import asyncio
import importlib
import logging
import pkgutil
from typing import Any
from uuid import UUID

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.security import APIKeyHeader
from jwt import InvalidTokenError
from pydantic import BaseModel, Field, SecretStr

from openhands import tools  # type: ignore[attr-defined]
from openhands.agent_server.models import ConversationInfo, Success
from openhands.analytics import get_analytics_service, resolve_analytics_context
from openhands.app_server import shared
from openhands.app_server.app_conversation.app_conversation_info_service import (
    AppConversationInfoService,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    ACP_SERVER_TAG_KEY,
    AppConversationInfo,
    ConversationTrigger,
)
from openhands.app_server.config import (
    depends_app_conversation_info_service,
    depends_event_service,
    depends_jwt_service,
    get_app_conversation_info_service,
    get_app_conversation_service,
    get_event_callback_service,
    get_global_config,
    get_sandbox_service,
)
from openhands.app_server.config_api.config_models import AppMode
from openhands.app_server.errors import AuthError
from openhands.app_server.event.event_parsing import parse_event_payload
from openhands.app_server.event.event_service import EventService
from openhands.app_server.event_callback.event_callback_models import EventCallback
from openhands.app_server.event_callback.event_callback_result_models import (
    EventCallbackResultStatus,
)
from openhands.app_server.event_callback.set_title_callback_processor import (
    SetTitleCallbackProcessor,
)
from openhands.app_server.event_callback.sql_event_callback_service import (
    StoredEventCallbackResult,
    invoke_callback,
)
from openhands.app_server.integrations.provider import ProviderType
from openhands.app_server.sandbox.sandbox_models import SandboxRecord
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.services.jwt_service import JwtAudience, JwtService
from openhands.app_server.user.auth_user_context import AuthUserContext
from openhands.app_server.user.specifiy_user_context import (
    ADMIN,
    USER_CONTEXT_ATTR,
    SandboxUserContext,
    SpecifyUserContext,
)
from openhands.app_server.user_auth.default_user_auth import DefaultUserAuth
from openhands.app_server.user_auth.user_auth import (
    get_for_user as get_user_auth_for_user,
)
from openhands.app_server.utils.docker_utils import (
    sandbox_url_for_app_server,
)
from openhands.sdk import ConversationExecutionStatus, Event
from openhands.sdk.event import ConversationStateUpdateEvent, ObservationEvent
from openhands.sdk.mcp.config import (
    MCPOAuthAuthCredential,
    MCPOAuthState,
    MCPOAuthStateResponse,
)
from openhands.sdk.settings import ACPAgentSettings
from openhands.sdk.settings.acp_providers import detect_acp_provider_by_command
from openhands.sdk.tool.builtins import SwitchLLMObservation
from server.auth.auth_error import AuthError as UserAuthError
from server.services.secret_lookup_service import is_active_org_member
from storage.mcp_config import serialize_mcp_config

router = APIRouter(prefix='/webhooks', tags=['Webhooks'])
event_service_dependency = depends_event_service(scope='function')
app_conversation_info_service_dependency = depends_app_conversation_info_service(
    scope='function'
)
jwt_dependency = depends_jwt_service()
app_mode = get_global_config().app_mode
_logger = logging.getLogger(__name__)


def _classify_error_type(error_message: str | None) -> str:
    """Classify conversation error into broad categories for dashboard filtering.

    Categories: budget_exceeded, model_error, runtime_error, timeout, user_cancelled, unknown.
    Uses best-effort string matching per CONTEXT.md decision.
    """
    if not error_message:
        return 'unknown'
    msg_lower = error_message.lower()
    if 'budget' in msg_lower or 'budgetexceeded' in msg_lower:
        return 'budget_exceeded'
    if 'timeout' in msg_lower or 'timed out' in msg_lower:
        return 'timeout'
    if 'cancel' in msg_lower:
        return 'user_cancelled'
    if any(
        kw in msg_lower
        for kw in ('model', 'llm', 'api key', 'rate limit', 'authentication')
    ):
        return 'model_error'
    return 'runtime_error'


def merge_conversation_tags(
    existing_tags: dict[str, str] | None,
    incoming_tags: dict[str, str] | None,
) -> dict[str, str]:
    """Merge conversation tags with incoming tags overriding existing ones.

    Args:
        existing_tags: Tags from the existing conversation (may be None)
        incoming_tags: Tags from the incoming update (may be None)

    Returns:
        Merged tags dict (empty dict if both inputs are None/empty)
    """
    existing = existing_tags or {}
    incoming = incoming_tags or {}
    return {**existing, **incoming}


async def _track_conversation_terminal(
    conversation_id: UUID,
    app_conversation_info: AppConversationInfo,
    events: list[Event],
    exec_status: ConversationExecutionStatus,
) -> None:
    """Track analytics for terminal conversation states.

    Handles BIZZ-03 (credit limit), BIZZ-05 (finished), and BIZZ-06 (errored) events.
    """
    analytics = get_analytics_service()
    if not analytics or not app_conversation_info.created_by_user_id:
        return

    ctx = await resolve_analytics_context(app_conversation_info.created_by_user_id)

    # Extract metrics
    metrics = app_conversation_info.metrics
    accumulated_cost = metrics.accumulated_cost if metrics else None
    prompt_tokens = (
        metrics.accumulated_token_usage.prompt_tokens
        if metrics and metrics.accumulated_token_usage
        else None
    )
    completion_tokens = (
        metrics.accumulated_token_usage.completion_tokens
        if metrics and metrics.accumulated_token_usage
        else None
    )

    is_error = exec_status in (
        ConversationExecutionStatus.ERROR,
        ConversationExecutionStatus.STUCK,
    )

    if is_error:
        # Find last error message
        error_message = None
        for ev in events:
            if isinstance(ev, ConversationStateUpdateEvent) and ev.key == 'last_error':
                error_message = str(ev.value)[:500] if ev.value else None

        error_type = _classify_error_type(error_message)

        # BIZZ-06: conversation errored
        analytics.track_conversation_errored(
            ctx=ctx,
            conversation_id=str(conversation_id),
            error_type=error_type,
            error_message=error_message,
            llm_model=app_conversation_info.llm_model,
            turn_count=None,
            terminal_state=exec_status.value,
        )

        # BIZZ-03: credit limit reached
        if error_type == 'budget_exceeded':
            analytics.track_credit_limit_reached(
                ctx=ctx,
                conversation_id=str(conversation_id),
                llm_model=app_conversation_info.llm_model,
            )
        return

    # BIZZ-05: conversation finished
    analytics.track_conversation_finished(
        ctx=ctx,
        conversation_id=str(conversation_id),
        terminal_state=exec_status.value,
        turn_count=None,
        accumulated_cost_usd=accumulated_cost,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        llm_model=app_conversation_info.llm_model,
        trigger=app_conversation_info.trigger.value
        if app_conversation_info.trigger
        else None,
    )


def detect_automation_trigger(
    current_trigger: ConversationTrigger | None,
    merged_tags: dict[str, str],
    conversation_id: str | None = None,
    sandbox_id: str | None = None,
) -> ConversationTrigger | None:
    """Detect if conversation should have AUTOMATION trigger based on tags.

    Only sets AUTOMATION trigger if:
    - Current trigger is None (don't override existing trigger)
    - Tags contain 'automationtrigger', 'automationid', or 'automationrunid' key

    Args:
        current_trigger: The existing trigger value (may be None)
        merged_tags: Merged tags dict to inspect
        conversation_id: Optional conversation ID for logging
        sandbox_id: Optional sandbox ID for logging

    Returns:
        ConversationTrigger.AUTOMATION if detected, otherwise current_trigger
    """
    if current_trigger is not None:
        return current_trigger

    if merged_tags and (
        merged_tags.get('automationtrigger')
        or merged_tags.get('automationid')
        or merged_tags.get('automationrunid')
    ):
        _logger.info(
            'Detected automation trigger from conversation tags',
            extra={
                'conversation_id': conversation_id,
                'sandbox_id': sandbox_id,
                'automationtrigger': merged_tags.get('automationtrigger'),
                'automationid': merged_tags.get('automationid'),
                'automationrunid': merged_tags.get('automationrunid'),
            },
        )
        return ConversationTrigger.AUTOMATION

    return None


async def valid_sandbox(
    request: Request,
    session_api_key: str = Depends(
        APIKeyHeader(name='X-Session-API-Key', auto_error=False)
    ),
) -> SandboxRecord:
    """Use a session api key for validation, and get a sandbox record. Subsequent actions
    are executed in the context of the owner of the sandbox"""
    if not session_api_key:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail='X-Session-API-Key header is required'
        )

    # Create a state which will be used internally only for this operation
    state = InjectorState()

    # Since we need access to all sandboxes, this is executed in the context of the admin.
    setattr(state, USER_CONTEXT_ATTR, ADMIN)
    async with get_sandbox_service(state) as sandbox_service:
        sandbox_record = await sandbox_service.get_sandbox_record_by_session_api_key(
            session_api_key
        )
        if sandbox_record is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail='Invalid session API key'
            )

        # Scope webhook services to the authenticated sandbox, independent of the
        # owner's currently selected organization.
        if sandbox_record.created_by_user_id:
            setattr(
                request.state,
                USER_CONTEXT_ATTR,
                SandboxUserContext(
                    user_id=sandbox_record.created_by_user_id,
                    sandbox_id=sandbox_record.id,
                ),
            )
        elif app_mode == AppMode.SAAS:
            _logger.error(
                'Sandbox had no user specified', extra={'sandbox_id': sandbox_record.id}
            )
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail='Sandbox had no user specified'
            )

        return sandbox_record


async def valid_conversation(
    conversation_id: UUID,
    sandbox_record: SandboxRecord = Depends(valid_sandbox),
) -> AppConversationInfo:
    state = InjectorState()
    setattr(state, USER_CONTEXT_ATTR, ADMIN)
    async with get_app_conversation_info_service(state) as service:
        app_conversation_info = await service.get_app_conversation_info(conversation_id)
    if not app_conversation_info:
        # Conversation does not yet exist - create a stub
        return AppConversationInfo(
            id=conversation_id,
            sandbox_id=sandbox_record.id,
            created_by_user_id=sandbox_record.created_by_user_id,
        )

    if (
        app_conversation_info.created_by_user_id != sandbox_record.created_by_user_id
        or app_conversation_info.sandbox_id != sandbox_record.id
    ):
        raise AuthError()

    return app_conversation_info


async def _resolve_acp_server_key(agent: Any, user_id: str | None) -> str | None:
    """Resolve the ACP provider key for a conversation whose tag is not yet set.

    Prefer the conversation's own launch command (``ACPAgent.acp_command``): it
    is authoritative for the agent that is actually running and matched against
    the SDK registry by ``detect_acp_provider_by_command``. Only fall back to the
    user's saved settings when the command is unknown (a custom server) or absent
    (older agent payloads) — the global setting can have drifted to a different
    provider since this conversation was created, so it must not win over the
    conversation's own command.
    """
    command = getattr(agent, 'acp_command', None)
    if command:
        provider = detect_acp_provider_by_command(command)
        if provider is not None:
            return provider.key
    try:
        settings_store = await shared.SettingsStoreImpl.get_instance(user_id)
        settings = await settings_store.load() if settings_store else None
        agent_settings = getattr(settings, 'agent_settings', None)
        if isinstance(agent_settings, ACPAgentSettings):
            return agent_settings.acp_server
    except Exception:
        _logger.warning(
            'Failed to resolve ACP server key for user %s', user_id, exc_info=True
        )
    return None


@router.post('/conversations')
async def on_conversation_update(
    conversation_info: ConversationInfo,
    sandbox_record: SandboxRecord = Depends(valid_sandbox),
    app_conversation_info_service: AppConversationInfoService = app_conversation_info_service_dependency,
) -> Success:
    """Webhook callback for when a conversation starts, pauses, resumes, or deletes.

    The ``ConversationInfo.agent`` field is an ``AgentBase`` discriminated
    union so both OpenHands (``Agent``) and ACP (``ACPAgent``) payloads are
    accepted on this single endpoint.
    """
    existing = await valid_conversation(
        conversation_info.id,
        sandbox_record,
    )

    # If the conversation is being deleted, no action is required...
    # Later we may consider deleting the conversation if it exists...
    if conversation_info.execution_status == ConversationExecutionStatus.DELETING:
        return Success()

    # Detect if this is a new conversation (stub has title=None)
    is_new_conversation = existing.title is None

    # Merge tags from incoming conversation info
    # SDK can set tags via Conversation(tags=...) which includes automation context
    merged_tags = merge_conversation_tags(existing.tags, conversation_info.tags)

    # Determine trigger - check if tags indicate automation, then fall back to existing
    trigger = detect_automation_trigger(
        existing.trigger,
        merged_tags,
        conversation_id=str(conversation_info.id),
        sandbox_id=sandbox_record.id,
    )
    # Conversations created directly on the agent-server (SDK, API clients)
    # carry no trigger; treat them as API-driven, like the V0 bearer default.
    if trigger is None:
        trigger = ConversationTrigger.REMOTE_API_KEY

    # Stamp client source tag from trigger so both the agent-server telemetry
    # and the enterprise analytics attribute the conversation consistently.
    if trigger == ConversationTrigger.GUI and not merged_tags.get('clientsource'):
        merged_tags['clientsource'] = 'agentcanvas'

    agent = conversation_info.agent
    if agent.agent_kind == 'acp':
        agent_kind = 'acp'
        # Prefer the model the ACP server is actually running
        # (``current_model_id`` is reconciled from the live session response, so
        # it survives a provider-side remap, e.g. gemini flash). Fall back to the
        # requested ``acp_model``, then to the last-persisted value so a payload
        # that hasn't reported a live model yet doesn't wipe a user-set model.
        llm_model = (
            conversation_info.current_model_id
            or getattr(agent, 'acp_model', None)
            or existing.llm_model
        )
        # Re-derive provider key if not in tags (race with creation, or a
        # conversation created directly on the agent-server).
        if ACP_SERVER_TAG_KEY not in merged_tags:
            acp_server_key = await _resolve_acp_server_key(
                agent, sandbox_record.created_by_user_id
            )
            if acp_server_key:
                merged_tags[ACP_SERVER_TAG_KEY] = acp_server_key
    else:
        # ``AgentBase.llm: LLM`` is non-optional on both arms of the union.
        agent_kind = 'openhands'
        llm_model = agent.llm.model

    app_conversation_info = AppConversationInfo(
        id=conversation_info.id,
        title=existing.title or f'Conversation {conversation_info.id.hex}',
        sandbox_id=sandbox_record.id,
        created_by_user_id=sandbox_record.created_by_user_id,
        llm_model=llm_model,
        agent_kind=agent_kind,
        # Git parameters
        selected_repository=existing.selected_repository,
        selected_branch=existing.selected_branch,
        git_provider=existing.git_provider,
        trigger=trigger,
        pr_number=existing.pr_number,
        pull_requests=existing.pull_requests,
        additional_repositories=existing.additional_repositories,
        # Preserve parent/child relationship and other metadata
        parent_conversation_id=existing.parent_conversation_id,
        metrics=conversation_info.stats.get_combined_metrics(),
        # Store merged tags (includes automation context, skills, etc.)
        tags=merged_tags,
    )
    if is_new_conversation:
        # The stub was read before the conversation's start saved its row; that
        # save may have happened since. Insert only if the row is still absent so
        # the stub's defaults never replace the start's title and repositories.
        is_new_conversation = (
            await app_conversation_info_service.create_app_conversation_info(
                app_conversation_info
            )
        )
        if not is_new_conversation:
            _logger.info(
                'Conversation %s was saved by its start while the webhook ran; '
                'keeping that row',
                conversation_info.id,
            )
    else:
        await app_conversation_info_service.save_app_conversation_info(
            app_conversation_info, preserve_title=True
        )

    # Register SetTitleCallbackProcessor for new conversations created via webhook.
    # This enables auto-titling for conversations created directly on the agent-server
    # (e.g., automation runs) that notify the app-server via webhook.
    if is_new_conversation:
        state = InjectorState()
        setattr(
            state,
            USER_CONTEXT_ATTR,
            SpecifyUserContext(sandbox_record.created_by_user_id),
        )
        async with get_event_callback_service(state) as event_callback_service:
            await event_callback_service.save_event_callback(
                EventCallback(
                    conversation_id=conversation_info.id,
                    event_kind=SetTitleCallbackProcessor.get_event_kind(),
                    processor=SetTitleCallbackProcessor(),
                )
            )

    return Success()


_LIVE_STATS_PULL_STATUSES = {
    ConversationExecutionStatus.FINISHED,
    ConversationExecutionStatus.IDLE,
    ConversationExecutionStatus.ERROR,
    ConversationExecutionStatus.STUCK,
}


async def _sync_live_conversation_stats(
    conversation_id: UUID,
    app_conversation_info: AppConversationInfo,
    app_conversation_info_service: AppConversationInfoService,
) -> None:
    """Pull the registry's combined stats from the agent-server at run end.

    Switched-in LLMs and ACP agents never attach a stats callback, so their
    spend emits no stats events; without this pull the persisted totals
    under-report whatever ran outside the startup-wired LLMs.
    """
    state = InjectorState()
    setattr(
        state,
        USER_CONTEXT_ATTR,
        SandboxUserContext(
            user_id=app_conversation_info.created_by_user_id,
            sandbox_id=app_conversation_info.sandbox_id,
        ),
    )
    async with get_app_conversation_service(state) as app_conversation_service:
        conversation = await app_conversation_service.get_app_conversation(
            conversation_id
        )
    if conversation is None or not conversation.conversation_url:
        return
    # Docker sandboxes advertise browser URLs; turn this one into a URL the
    # app server can reach, as the title processor does.
    conversation_url = sandbox_url_for_app_server(conversation.conversation_url)
    headers = {}
    if conversation.session_api_key:
        headers['X-Session-API-Key'] = conversation.session_api_key
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(conversation_url, headers=headers)
        response.raise_for_status()
        info = ConversationInfo.model_validate(response.json())
    if info.stats and info.stats.usage_to_metrics:
        await app_conversation_info_service.update_conversation_statistics(
            conversation_id, info.stats
        )


@router.post('/events/{conversation_id}')
async def on_event(
    background_tasks: BackgroundTasks,
    conversation_id: UUID,
    events: list[Any] = Body(...),
    app_conversation_info: AppConversationInfo = Depends(valid_conversation),
    app_conversation_info_service: AppConversationInfoService = app_conversation_info_service_dependency,
    event_service: EventService = event_service_dependency,
) -> Success:
    """Webhook callback for when event stream events occur."""
    parsed_events = [parse_event_payload(event) for event in events]

    try:
        # Save events...
        await asyncio.gather(
            *[
                event_service.save_event(conversation_id, event)
                for event in parsed_events
            ]
        )

        # Process stats events for V1 conversations
        for event in parsed_events:
            if isinstance(event, ConversationStateUpdateEvent) and event.key == 'stats':
                await app_conversation_info_service.process_stats_event(
                    event, conversation_id
                )

        # Reflect an agent-initiated LLM switch (via the built-in SwitchLLMTool)
        # on the conversation record. The tool emits a ``SwitchLLMObservation``
        # carrying the new ``active_model``; unlike the explicit switch_profile
        # route, nothing else persists it here, so the chat header and
        # switch-profile button would otherwise stay stale until the next full
        # conversation-info webhook (which only fires on start/pause/interrupt/
        # delete, never mid-run). ``active_model`` is only set on success.
        switched_model: str | None = None
        for event in parsed_events:
            if (
                isinstance(event, ObservationEvent)
                and isinstance(event.observation, SwitchLLMObservation)
                and event.observation.active_model
            ):
                switched_model = event.observation.active_model
        if switched_model and app_conversation_info.llm_model != switched_model:
            info = await app_conversation_info_service.get_app_conversation_info(
                conversation_id
            )
            if info is not None and info.llm_model != switched_model:
                info.llm_model = switched_model
                await app_conversation_info_service.save_app_conversation_info(info)

        # Analytics: conversation terminal state detection
        # Also persist execution status to database for dashboard queries
        run_ended = False
        for event in parsed_events:
            if not isinstance(event, ConversationStateUpdateEvent):
                continue
            if event.key != 'execution_status':
                continue
            try:
                exec_status = ConversationExecutionStatus(event.value)
                # Persist execution status for org-wide dashboard
                await app_conversation_info_service.update_execution_status(
                    conversation_id, exec_status.value
                )
                if exec_status in _LIVE_STATS_PULL_STATUSES:
                    run_ended = True
                if exec_status.is_terminal():
                    await _track_conversation_terminal(
                        conversation_id,
                        app_conversation_info,
                        parsed_events,
                        exec_status,
                    )
            except Exception:
                _logger.exception(
                    'analytics:conversation_terminal:failed', stack_info=True
                )

        if run_ended:
            try:
                await _sync_live_conversation_stats(
                    conversation_id,
                    app_conversation_info,
                    app_conversation_info_service,
                )
            except Exception:
                _logger.warning('live_stats_pull_failed', exc_info=True)

        background_tasks.add_task(
            _run_callbacks_in_bg_and_close,
            conversation_id,
            app_conversation_info.created_by_user_id,
            parsed_events,
        )

    except Exception:
        _logger.exception('Error in webhook', stack_info=True)

    return Success()


async def _resolve_user_context(user_id: str | None) -> AuthUserContext:
    """Resolve a UserContext from a user_id, falling back to DefaultUserAuth in OSS mode."""
    user_auth = await get_user_auth_for_user(user_id) if user_id else DefaultUserAuth()
    return AuthUserContext(user_auth=user_auth)


def _is_unscoped_secret_lookup_token(payload: dict[str, Any]) -> bool:
    # Org-bound references were never minted without an audience.
    return (
        'user_id' in payload
        and ('provider_type' in payload or 'oauth_provider_id' in payload)
        and 'org_id' not in payload
        and 'secret_name' not in payload
    )


def _is_scoped_org_reference(payload: dict[str, Any]) -> bool:
    """An org-bound reference names its audience and expires."""
    return payload.get('aud') == JwtAudience.SECRET_LOOKUP.value and isinstance(
        payload.get('exp'), int | float
    )


async def _resolve_org_user_context(user_id: str, org_id: UUID) -> AuthUserContext:
    """A user context that reads secrets as a member of ``org_id``.

    Each redemption checks the rule the secret-lookups route applied when it
    signed the reference: an active membership of a user who is neither
    disabled nor being deleted, with no super-admin exception. Anyone else
    gets 403 before any secret is read.
    """
    if not await is_active_org_member(user_id, org_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    try:
        user_auth = await get_user_auth_for_user(user_id)
    except UserAuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN) from exc
    set_override = getattr(user_auth, 'set_effective_org_id_override', None)
    get_effective_org_id = getattr(user_auth, 'get_effective_org_id', None)
    if set_override is None or get_effective_org_id is None:
        # Only the SaaS server signs org-bound references.
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    set_override(org_id)
    await get_effective_org_id()
    return AuthUserContext(user_auth=user_auth)


_SECRET_LOOKUP_TARGETS = ('provider_type', 'oauth_provider_id', 'secret_name')


async def _get_org_secret(payload: dict[str, Any], user_id: str) -> Response:
    """Resolve a reference signed for one org by the secret-lookups service route.

    It names exactly one secret: a provider type, an OAuth provider row or a
    custom secret of the org.
    """
    targets = [key for key in _SECRET_LOOKUP_TARGETS if key in payload]
    if len(targets) != 1:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    try:
        org_id = UUID(str(payload['org_id']))
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED) from exc
    user_context = await _resolve_org_user_context(user_id, org_id)

    secret: str | SecretStr | None
    if targets == ['secret_name']:
        kind, name = 'custom', str(payload['secret_name'])
        secret = None
        if name != 'OPENHANDS_API_KEY':
            secrets_store = await user_context.user_auth.get_secrets_store()
            secrets = await secrets_store.load()
            custom_secret = secrets.custom_secrets.get(name) if secrets else None
            secret = custom_secret.secret if custom_secret else None
    elif targets == ['oauth_provider_id']:
        kind, name = 'oauth', str(payload['oauth_provider_id'])
        secret = await user_context.get_named_oauth_secret_value(
            int(payload['oauth_provider_id'])
        )
    else:
        provider_type = ProviderType(payload['provider_type'])
        kind, name = 'provider', provider_type.value
        try:
            provider_tokens = await user_context.get_provider_tokens()
        except UserAuthError:
            # The provider's refresh token stopped working: not connected.
            provider_tokens = None
        secret = (
            await user_context.get_latest_token(provider_type)
            if provider_tokens and provider_type in provider_tokens
            else None
        )

    # Audit trail of every org-bound resolution; never the value.
    _logger.info(
        'webhooks:secret_lookup',
        extra={
            'user_id': user_id,
            'org_id': str(org_id),
            'secret_kind': kind,
            'secret_name': name,
            'found': secret is not None,
        },
    )
    if secret is None:
        raise HTTPException(404, 'No such secret')
    if isinstance(secret, SecretStr):
        secret = secret.get_secret_value()
    return Response(content=secret, media_type='text/plain')


@router.get('/secrets')
async def get_secret(
    access_token: str = Depends(APIKeyHeader(name='X-Access-Token', auto_error=False)),
    jwt_service: JwtService = jwt_dependency,
) -> Response:
    """Given an access token, retrieve a user secret. The access token
    is limited by user and either an OAuth provider row (``oauth_provider_id``)
    or a provider type, and may include a timeout, limiting the damage in the
    event that a token is ever leaked.

    A token from the secret-lookups service route also names an org
    (``org_id``): the secret is read as a member of that org, and it can name
    one of that org's custom secrets (``secret_name``). Such a reference must
    carry the ``SECRET_LOOKUP`` audience and an expiry, and it resolves only
    while its principal is an active member of the org."""
    if not access_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    try:
        payload = jwt_service.verify_jws_token(
            access_token,
            audience=JwtAudience.SECRET_LOOKUP,
            # TODO(OHE-3812): remove after 2026-12-15, see JwtService.verify_jws_token.
            accept_unscoped=_is_unscoped_secret_lookup_token,
        )
        user_id = payload['user_id']
        if 'org_id' in payload:
            if not _is_scoped_org_reference(payload):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED)
            return await _get_org_secret(payload, user_id)
        if 'secret_name' in payload:
            # Only org-bound references name a custom secret.
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)
        user_context = await _resolve_user_context(user_id)

        secret: str | SecretStr | None
        if 'oauth_provider_id' in payload:
            secret = await user_context.get_named_oauth_secret_value(
                int(payload['oauth_provider_id'])
            )
        else:
            provider_type = ProviderType(payload['provider_type'])
            secret = await user_context.get_latest_token(provider_type)
        if secret is None:
            raise HTTPException(404, 'No such provider')
        if isinstance(secret, SecretStr):
            secret_value = secret.get_secret_value()
        else:
            secret_value = secret

        return Response(content=secret_value, media_type='text/plain')
    except InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)


class MCPOAuthStateUpdate(BaseModel):
    """OAuth state a sandbox refreshed for one of its owner's MCP servers."""

    server_url: str = Field(..., min_length=1)
    oauth_state: MCPOAuthStateResponse


@router.post('/mcp-oauth-state')
async def on_mcp_oauth_state_update(
    update: MCPOAuthStateUpdate,
    sandbox_record: SandboxRecord = Depends(valid_sandbox),
) -> Success:
    """Persist MCP OAuth tokens refreshed inside a sandbox.

    Hosted conversations get the owner's ``mcp_config`` inline, so a token
    FastMCP refreshes there would otherwise be lost with the sandbox and the
    next conversation would start from the previous refresh token, which
    providers that rotate refresh tokens have already revoked. The agent
    server posts the refreshed state here (``MCPSettingsOAuthTokenStore``);
    it replaces ``auth.state`` on the owner's ``oauth2`` server with the same
    URL. Settings resolve like the other webhook lookups, through the owner's
    current organization. When no personal server matches, the MCP listings of
    the sandbox's organization at that URL take the state instead (OHE-3091):
    an org-managed listing itself, a user-managed one on the owner's row.
    """
    settings_store = await shared.SettingsStoreImpl.get_instance(
        sandbox_record.created_by_user_id
    )
    settings = await settings_store.load()
    if settings is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail='No settings for the sandbox owner'
        )
    server_url = update.server_url.rstrip('/')
    mcp_config = settings.agent_settings.mcp_config
    matches = [
        name
        for name, server in mcp_config.items()
        if server.url is not None
        and server.url.rstrip('/') == server_url
        and isinstance(server.auth, MCPOAuthAuthCredential)
    ]
    if not matches:
        org_matches = await _write_back_org_mcp_oauth_state(
            sandbox_record, server_url, update.oauth_state
        )
        if not org_matches:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail='No OAuth MCP server matches server_url',
            )
        _logger.info(
            'mcp_oauth:state_written_back',
            extra={'sandbox_id': sandbox_record.id, 'org_servers': org_matches},
        )
        return Success()
    # A full map without ``null`` entries replaces the stored catalog wholesale
    # (``Settings.update``), so every server is resent with only the matching
    # ones' ``auth.state`` swapped for the refreshed state.
    serialized = serialize_mcp_config(mcp_config) or {}
    state = update.oauth_state.model_dump(mode='json', exclude_none=True)
    for name in matches:
        serialized[name]['auth']['state'] = state
    settings.update({'agent_settings_diff': {'mcp_config': serialized}})
    await settings_store.store(settings)
    _logger.info(
        'mcp_oauth:state_written_back',
        extra={'sandbox_id': sandbox_record.id, 'servers': matches},
    )
    return Success()


async def _write_back_org_mcp_oauth_state(
    sandbox_record: SandboxRecord,
    server_url: str,
    oauth_state: MCPOAuthStateResponse,
) -> list[str]:
    """Store refreshed OAuth state on the organization MCP listings at the URL.

    The org is the one the sandbox launched under, falling back to its owner's
    current one for sandboxes that predate that binding. Returns the names of
    the listings written. Lazy import: the org store binds the hosted database.
    """
    from storage.org_mcp_servers_store import OrgMcpServersStore
    from storage.user_store import UserStore

    if not sandbox_record.created_by_user_id:
        return []
    user = await UserStore.get_user_by_id(sandbox_record.created_by_user_id)
    if user is None:
        return []
    state = MCPOAuthState.model_validate(
        oauth_state.model_dump(mode='json', exclude_none=True)
    )
    org_id = sandbox_record.credential_org_id or user.current_org_id
    return await OrgMcpServersStore(org_id).write_back_oauth_state(
        user.id, server_url, state
    )


async def _run_callbacks_in_bg_and_close(
    conversation_id: UUID,
    user_id: str | None,
    events: list[Event],
):
    """Run all active callbacks for the given events.

    The ``SQLEventCallbackService`` opens its own short-lived ``AsyncSession``
    per method call, so the SQLAlchemy pool connection is only checked out for
    the SELECT (in :meth:`get_active_callbacks`) and the COMMIT (in
    :meth:`persist_callback_results`). The slow callback processors run with
    **no** connection held — which is what stops webhook bursts from
    exhausting the 25+10 pool and starving subsequent ``asyncpg.connect``
    attempts.
    """
    state = InjectorState()
    setattr(state, USER_CONTEXT_ATTR, SpecifyUserContext(user_id=user_id))

    async with get_event_callback_service(state) as service:
        for event in events:
            callbacks = await service.get_active_callbacks(conversation_id, event)
            if not callbacks:
                continue
            outcomes = await asyncio.gather(
                *[invoke_callback(cb, conversation_id, event) for cb in callbacks],
                return_exceptions=True,
            )
            normalised: list[StoredEventCallbackResult | None] = []
            for callback, outcome in zip(callbacks, outcomes, strict=False):
                if isinstance(outcome, BaseException):
                    _logger.exception(
                        f'Exception in callback {callback.id}', stack_info=True
                    )
                    normalised.append(
                        StoredEventCallbackResult(
                            status=EventCallbackResultStatus.ERROR,
                            event_callback_id=callback.id,
                            event_id=event.id,
                            conversation_id=conversation_id,
                            detail=str(outcome),
                        )
                    )
                else:
                    normalised.append(outcome)
            await service.persist_callback_results(callbacks, normalised)


def _import_all_tools():
    """We need to import all tools so that they are available for deserialization in webhooks."""
    for _, name, is_pkg in pkgutil.walk_packages(tools.__path__, tools.__name__ + '.'):
        if is_pkg:  # Check if it's a subpackage
            try:
                importlib.import_module(name)
            except ImportError:
                _logger.exception(
                    f"Warning: Could not import subpackage '{name}'",
                    stack_info=True,
                )


_import_all_tools()
