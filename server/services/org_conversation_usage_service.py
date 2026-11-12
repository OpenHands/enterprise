"""Read-only usage detail of one organization conversation.

Built only from the conversation record and the creator's retained event
history: no sandbox or runtime is contacted and nothing is written.
"""

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, Literal, cast
from uuid import UUID

from fastapi import Request
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.agent_server.models import EventSortOrder
from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    _parse_event_timestamp,
)
from openhands.app_server.event.event_service import EventService
from openhands.app_server.services.injector import Injector, InjectorState
from openhands.sdk.event import ActionEvent, Event, ObservationEvent
from openhands.sdk.event.conversation_state import (
    FULL_STATE_KEY,
    ConversationStateUpdateEvent,
)
from openhands.sdk.llm.utils.metrics import MetricsSnapshot
from openhands.tools.task.definition import TaskAction, TaskObservation
from server.routes.org_models import (
    ConversationTaskCall,
    ConversationUsageBucket,
    ConversationUsageCounters,
    ConversationUsageGap,
    OrgConversationUsageResponse,
)
from storage.org import Org
from storage.stored_conversation_metadata import StoredConversationMetadata
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas

# Per-request bounds on retained-history reads.
EVENT_PAGE_SIZE = 100
MAX_EVENTS_PER_KIND = 1000
SCAN_TIME_BUDGET_SECONDS = 10.0

# Identifier allowlists (no whitespace), so free text cannot pass through.
_TASK_ID_RE = re.compile(r'task_[0-9a-f]{8}')
_USAGE_ID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:@/+-]{0,127}')
_AGENT_TYPE_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')

# Stats are stored as MetricsSnapshot dumps. Parsing them as Metrics would
# turn unset token usage into zeros and lose the unknown/zero distinction.
_SNAPSHOTS = TypeAdapter(dict[str, MetricsSnapshot])


def _valid(value: str | None, pattern: re.Pattern[str]) -> str | None:
    return value if value and pattern.fullmatch(value) else None


def _bucket(usage_id: str, snapshot: MetricsSnapshot) -> ConversationUsageBucket:
    kind: Literal['main_agent', 'condenser', 'task', 'other']
    task_id = None
    if usage_id == 'agent' or usage_id.startswith('profile:'):
        kind = 'main_agent'
    elif usage_id == 'condenser':
        kind = 'condenser'
    elif usage_id.startswith('task:'):
        kind = 'task'
        task_id = _valid(usage_id.removeprefix('task:'), _TASK_ID_RE)
    else:
        kind = 'other'

    usage = snapshot.accumulated_token_usage
    # Task buckets are merged Metrics that keep the placeholder model name.
    model = next(
        (
            m
            for m in (snapshot.model_name, usage and usage.model)
            if m and m != 'default'
        ),
        None,
    )
    counters = ConversationUsageCounters(cost=snapshot.accumulated_cost)
    if usage is not None:
        counters = ConversationUsageCounters(
            cost=snapshot.accumulated_cost,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            reasoning_tokens=usage.reasoning_tokens,
        )
    return ConversationUsageBucket(
        usage_id=_valid(usage_id, _USAGE_ID_RE),
        kind=kind,
        task_id=task_id,
        model=model,
        usage=counters,
    )


def _snapshot(event: ConversationStateUpdateEvent) -> dict[str, MetricsSnapshot]:
    """Per-usage stats of a ``stats`` update, or of the stats sub-tree only of
    a ``full_state`` update."""
    value = event.value
    if event.key == FULL_STATE_KEY and isinstance(value, dict):
        value = value.get('stats')
    elif event.key != 'stats':
        return {}
    if not isinstance(value, dict) or not isinstance(
        value.get('usage_to_metrics'), dict
    ):
        return {}
    return _SNAPSHOTS.validate_python(value['usage_to_metrics'])


@dataclass
class _History:
    snapshot: dict[str, MetricsSnapshot] = field(default_factory=dict)
    snapshot_event: ConversationStateUpdateEvent | None = None
    calls: dict[str, ActionEvent] = field(default_factory=dict)
    results: dict[str, ObservationEvent] = field(default_factory=dict)
    truncated: bool = False


async def _newest(
    events: EventService, conversation_id: UUID, kind: str, deadline: float
) -> AsyncGenerator[Event | None, None]:
    """Events of one kind, newest first. Yields None if a bound cut the read."""
    page_id = None
    seen = 0
    while seen < MAX_EVENTS_PER_KIND:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            page = await asyncio.wait_for(
                events.search_events(
                    conversation_id=conversation_id,
                    kind__eq=kind,  # type: ignore[arg-type]
                    sort_order=EventSortOrder.TIMESTAMP_DESC,
                    page_id=page_id,
                    limit=min(EVENT_PAGE_SIZE, MAX_EVENTS_PER_KIND - seen),
                ),
                timeout=remaining,
            )
        except TimeoutError:
            break
        for item in page.items:
            yield item
        seen += len(page.items)
        page_id = page.next_page_id
        if not page_id:
            return
    yield None


async def _read_history(events: EventService, conversation_id: UUID) -> _History:
    history = _History()
    deadline = time.monotonic() + SCAN_TIME_BUDGET_SECONDS

    # Only the newest snapshot is used: each carries the full cumulative
    # registry, so merging older ones could resurrect removed buckets.
    async for item in _newest(
        events, conversation_id, 'ConversationStateUpdateEvent', deadline
    ):
        if item is None:
            history.truncated = True
        elif isinstance(item, ConversationStateUpdateEvent) and (
            snapshot := _snapshot(item)
        ):
            history.snapshot = snapshot
            history.snapshot_event = item
            break

    async for item in _newest(events, conversation_id, 'ObservationEvent', deadline):
        if item is None:
            history.truncated = True
        elif isinstance(item, ObservationEvent) and isinstance(
            item.observation, TaskObservation
        ):
            history.results[item.action_id] = item

    async for item in _newest(events, conversation_id, 'ActionEvent', deadline):
        if item is None:
            history.truncated = True
        elif isinstance(item, ActionEvent) and isinstance(item.action, TaskAction):
            history.calls[item.id] = item
    return history


def _task_calls(
    history: _History, conversation_running: bool, gaps: set[ConversationUsageGap]
) -> list[ConversationTaskCall]:
    calls = []
    for action_id in history.calls.keys() | history.results.keys():
        call = history.calls.get(action_id)
        result = history.results.get(action_id)
        action = cast(TaskAction, call.action) if call else None
        observation = cast(TaskObservation, result.observation) if result else None

        if observation is not None:
            status = str(observation.status)
            if status not in ('completed', 'error'):
                status = 'unknown'
        elif conversation_running and not history.truncated:
            status = 'running'
            # Task usage reaches the parent registry only when a call ends.
            gaps.add('task_usage_pending')
        else:
            status = 'unknown'
        if status == 'unknown':
            gaps.add('task_result_missing')

        started_at = _parse_event_timestamp(call.timestamp) if call else None
        ended_at = _parse_event_timestamp(result.timestamp) if result else None
        duration = None
        if started_at and ended_at:
            if ended_at >= started_at:
                duration = (ended_at - started_at).total_seconds()
            else:
                gaps.add('inconsistent_task_timestamps')

        calls.append(
            ConversationTaskCall(
                # A resume call names its task; a result names the task it ran.
                task_id=_valid(
                    observation.task_id if observation else None, _TASK_ID_RE
                )
                or _valid(action.resume if action else None, _TASK_ID_RE),
                agent_type=_valid(
                    action.subagent_type if action else None, _AGENT_TYPE_RE
                )
                or _valid(
                    observation.subagent if observation else None, _AGENT_TYPE_RE
                ),
                resumed=action.resume is not None if action else None,
                status=status,  # type: ignore[arg-type]
                started_at=started_at,
                ended_at=ended_at,
                duration_seconds=duration,
            )
        )
    # A call without a valid type (e.g. a resume) takes its task's known type.
    known_types = {c.task_id: c.agent_type for c in calls if c.task_id and c.agent_type}
    for c in calls:
        if c.task_id and not c.agent_type:
            c.agent_type = known_types.get(c.task_id)
    calls.sort(key=lambda c: c.started_at.isoformat() if c.started_at else '~')
    return calls


def _sum(buckets: list[ConversationUsageBucket]) -> ConversationUsageCounters:
    """Per-field sum; None unless every bucket reported the field."""
    total = ConversationUsageCounters()
    for name in ConversationUsageCounters.model_fields:
        values = [getattr(b.usage, name) for b in buckets]
        if None not in values:
            setattr(total, name, sum(values))
    return total


@dataclass
class OrgConversationUsageService:
    db_session: AsyncSession

    async def is_enabled(self, org_id: UUID) -> bool:
        """Whether the org opted in; read per request, never cached."""
        enabled = await self.db_session.scalar(
            select(Org.enable_conversation_usage_detail).where(Org.id == org_id)
        )
        return enabled is True

    async def get_conversation_usage(
        self, org_id: UUID, conversation_id: str
    ) -> OrgConversationUsageResponse | None:
        """None unless this is a non-deleted V1 conversation of ``org_id``."""
        try:
            conversation_uuid = UUID(conversation_id)
        except ValueError:
            return None
        # Org ownership is checked here, before any history is read. The
        # creator whose event namespace is read comes from this record, never
        # from the caller.
        row = (
            await self.db_session.execute(
                select(
                    StoredConversationMetadata, StoredConversationMetadataSaas.user_id
                )
                .join(
                    StoredConversationMetadataSaas,
                    StoredConversationMetadata.conversation_id
                    == StoredConversationMetadataSaas.conversation_id,
                )
                .where(StoredConversationMetadataSaas.org_id == org_id)
                .where(
                    StoredConversationMetadata.conversation_id == str(conversation_uuid)
                )
                .where(StoredConversationMetadata.conversation_version == 'V1')
                .where(StoredConversationMetadata.deleted_at.is_(None))
            )
        ).one_or_none()
        if row is None:
            return None
        metadata, creator_id = row

        async with self._creator_event_service(str(creator_id)) as events:
            history = await _read_history(events, conversation_uuid)

        gaps: set[ConversationUsageGap] = set()
        if history.truncated:
            gaps.add('scan_limit_reached')
        buckets = [_bucket(k, v) for k, v in sorted(history.snapshot.items())]
        task_calls = _task_calls(
            history,
            metadata.execution_status in ('running', 'waiting_for_confirmation'),
            gaps,
        )
        bucket_tasks = {b.task_id for b in buckets if b.task_id}
        call_tasks = {c.task_id for c in task_calls if c.task_id}
        if call_tasks - bucket_tasks:
            gaps.add('task_usage_missing')
        if bucket_tasks - call_tasks:
            gaps.add('task_calls_outside_history')

        totals = ConversationUsageCounters(
            cost=metadata.accumulated_cost,
            prompt_tokens=metadata.prompt_tokens,
            completion_tokens=metadata.completion_tokens,
            cache_read_tokens=metadata.cache_read_tokens,
            cache_write_tokens=metadata.cache_write_tokens,
            reasoning_tokens=metadata.reasoning_tokens,
        )
        completeness: Literal['full', 'partial', 'totals_only']
        if not buckets:
            gaps.add('no_stats_snapshot')
            completeness = 'totals_only'
        else:
            # "full" means the snapshot explains the recorded totals, not
            # merely that the read reached the end of retained history.
            observed = _sum(buckets)
            for name in ConversationUsageCounters.model_fields:
                seen, recorded = getattr(observed, name), getattr(totals, name)
                if seen is None:
                    gaps.add('unknown_counters')
                elif recorded is not None and recorded - seen > 1e-6:
                    gaps.add('totals_ahead_of_snapshot')
            completeness = 'partial' if gaps else 'full'

        snapshot_event = history.snapshot_event
        return OrgConversationUsageResponse(
            conversation_id=metadata.conversation_id,
            totals=totals,
            completeness=completeness,
            incomplete_reasons=sorted(gaps),
            latest_snapshot_at=_parse_event_timestamp(snapshot_event.timestamp)
            if snapshot_event
            else None,
            buckets=buckets,
            task_calls=task_calls,
        )

    def _creator_event_service(self, creator_id: str):
        # Local imports to avoid circular dependencies.
        from openhands.app_server.config import get_event_service
        from openhands.app_server.services.db_session_injector import DB_SESSION_ATTR
        from openhands.app_server.user.specifiy_user_context import (
            USER_CONTEXT_ATTR,
            SpecifyUserContext,
        )

        state = InjectorState()
        setattr(state, USER_CONTEXT_ATTR, SpecifyUserContext(user_id=creator_id))
        setattr(state, DB_SESSION_ATTR, self.db_session)
        return get_event_service(state)


class OrgConversationUsageServiceInjector(Injector[OrgConversationUsageService]):
    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[OrgConversationUsageService, None]:
        from openhands.app_server.config import get_db_session

        async with get_db_session(state, request) as db_session:
            yield OrgConversationUsageService(db_session=db_session)
