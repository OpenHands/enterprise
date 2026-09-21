"""EventCallbackProcessor that detects and records MEMORY.md updates.

When an agent edits its persistent memory file (MEMORY.md) via the
file_editor tool during a conversation, this processor captures the
before/after content so the change can be propagated to other
conversations/sessions sharing the same memory tiers.

Known coverage limitation:
    This approach only detects changes made via the file_editor tool. The
    agent can also write files through the terminal tool (e.g.
    `echo "..." >> MEMORY.md`, `sed -i`, ...), which would not be caught
    here. This is an accepted tradeoff for v1: the persistent-memory prompt
    guidance steers the agent toward the file editor for file maintenance.
    A future enhancement could add a PostToolUse hook or a session-end diff
    as a backstop.
"""

import json
import logging
from typing import ClassVar
from uuid import UUID

from openhands.app_server.event_callback.event_callback_models import (
    EventCallback,
    EventCallbackProcessor,
    EventKind,
)
from openhands.app_server.event_callback.event_callback_result_models import (
    EventCallbackResult,
    EventCallbackResultStatus,
)
from openhands.sdk import Event
from openhands.sdk.event import ObservationEvent

# The two memory-file relative paths the SDK persistent-memory system reads.
# Project tier: <workspace>/.openhands/memory/MEMORY.md
# User tier:    <user_persistence_dir>/memory/MEMORY.md
# The SDK exposes MEMORY_INDEX_RELPATH for the project tier; the user tier is
# the trailing `memory/MEMORY.md` (no `.openhands/` prefix). We match on the
# path *ending* with either relpath so absolute paths from the runtime are
# handled regardless of the workspace or persistence dir.
_PROJECT_MEMORY_RELPATH = '.openhands/memory/MEMORY.md'
_USER_MEMORY_RELPATH = 'memory/MEMORY.md'

_logger = logging.getLogger(__name__)


class MemoryChangeCallbackProcessor(EventCallbackProcessor):
    """Detect and record MEMORY.md updates made via the file_editor tool.

    Unlike ``SetTitleCallbackProcessor`` this processor never self-disables:
    a conversation can update its memory multiple times, so the callback must
    stay ``ACTIVE`` for the lifetime of the conversation.
    """

    event_kind: ClassVar[EventKind] = 'ObservationEvent'

    async def __call__(
        self,
        conversation_id: UUID,
        callback: EventCallback,
        event: Event,
    ) -> EventCallbackResult | None:
        if not isinstance(event, ObservationEvent):
            return None

        if event.tool_name != 'file_editor':
            return None

        # Importing FileEditorObservation triggers openhands.tools, which
        # _import_all_tools() at the bottom of webhook_router.py already does
        # at import time; guard the isinstance check regardless.
        from openhands.tools.file_editor.definition import FileEditorObservation

        observation = event.observation
        if not isinstance(observation, FileEditorObservation):
            return None

        path = observation.path
        if path is None:
            return None

        if not _is_memory_path(path):
            return None

        # Read-only and failed edits are not memory changes.
        if observation.command == 'view':
            return None
        if observation.is_error:
            return None

        # Infer the memory tier from the path. A path ending with the
        # project relpath is the project tier; a path ending with the user
        # relpath (and NOT the project relpath) is the user tier. NOTE: when
        # the user persistence dir defaults to ~/.openhands, a user-tier path
        # collides with the project relpath suffix and is classified as
        # project tier here -- this is the accepted ambiguity of a
        # path-only heuristic.
        normalized_path = path.replace('\\', '/')
        if normalized_path.endswith(_PROJECT_MEMORY_RELPATH):
            tier = 'project'
        else:
            tier = 'user'

        old_content = observation.old_content
        new_content = observation.new_content

        _logger.info(
            'Memory file changed in conversation %s (tier=%s, path=%s)',
            conversation_id,
            tier,
            path,
        )

        detail = json.dumps(
            {
                'memory_tier': tier,
                'path': path,
                'old_content': old_content,
                'new_content': new_content,
            },
            ensure_ascii=False,
        )

        return EventCallbackResult(
            status=EventCallbackResultStatus.SUCCESS,
            event_callback_id=callback.id,
            event_id=event.id,
            conversation_id=conversation_id,
            detail=detail,
        )


def _is_memory_path(path: str) -> bool:
    """Return True if ``path`` points at a MEMORY.md memory index file.

    Matches the project tier relpath (``.openhands/memory/MEMORY.md``) or the
    user tier relpath (``memory/MEMORY.md``) as a path suffix.
    """
    normalized = path.replace('\\', '/')
    if normalized.endswith(_PROJECT_MEMORY_RELPATH):
        return True
    return normalized.endswith(_USER_MEMORY_RELPATH)
