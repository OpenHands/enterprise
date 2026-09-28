"""EventCallbackProcessor that detects and records MEMORY.md updates.

When an agent edits its persistent memory file (MEMORY.md) via the
file_editor tool during a conversation, this processor captures the
before/after content and stores the new content in the user record's
``memory_context`` column so it can be injected into future conversations.

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
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.specifiy_user_context import ADMIN, USER_CONTEXT_ATTR
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
    """Detect MEMORY.md updates made via the file_editor tool and persist
    the new content to the user record.

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

        # Persist the new memory content to the user record so it can be
        # injected into future conversations. The user_id is resolved from
        # the conversation's created_by_user_id field.
        await self._store_memory_context(conversation_id, new_content)

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

    async def _store_memory_context(
        self, conversation_id: UUID, memory_context: str | None
    ) -> None:
        """Write the new memory content to the creating user's record.

        Uses ADMIN context to look up the conversation, then performs a
        column-specific UPDATE on the user table so concurrent settings
        saves are not affected.
        """
        from uuid import UUID as UUIDType

        from sqlalchemy import update

        from openhands.app_server.config import get_app_conversation_service
        from storage.database import a_session_maker
        from storage.user import User

        # Resolve the user_id from the conversation record.
        state = InjectorState()
        setattr(state, USER_CONTEXT_ATTR, ADMIN)
        async with get_app_conversation_service(state) as app_conversation_service:
            app_conversation = await app_conversation_service.get_app_conversation(
                conversation_id
            )
        if app_conversation is None:
            _logger.warning(
                'Cannot store memory context: conversation %s not found',
                conversation_id,
            )
            return

        user_id_str = app_conversation.created_by_user_id
        if not user_id_str:
            _logger.warning(
                'Conversation %s has no created_by_user_id; '
                'cannot store memory context',
                conversation_id,
            )
            return

        try:
            user_uuid = UUIDType(user_id_str)
        except ValueError:
            _logger.warning(
                'Invalid user_id %s for conversation %s',
                user_id_str,
                conversation_id,
            )
            return

        # Column-specific update: only touch ``memory_context`` so
        # concurrent settings saves are not affected.
        async with a_session_maker() as session:
            await session.execute(
                update(User)
                .where(User.id == user_uuid)
                .values(memory_context=memory_context)
            )
            await session.commit()

        _logger.info(
            'Stored memory_context (%d chars) for user %s from conversation %s',
            len(memory_context) if memory_context else 0,
            user_id_str,
            conversation_id,
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
