"""SQL implementation of SharedConversationInfoService.

This implementation provides read-only access to shared conversations:
- Direct database access without the per-user conversation filters
- Serves conversations marked public, plus automation-triggered conversations
  in an org the (optional) authenticated viewer is a member of
- Full async/await support using SQL async db_sessions
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import AsyncGenerator
from uuid import UUID

from fastapi import Request
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from openhands.agent_server.utils import utc_now
from openhands.app_server.app_conversation.app_conversation_models import (
    ConversationTrigger,
)
from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    StoredConversationMetadata,
)
from openhands.app_server.errors import AuthError as AppAuthError
from openhands.app_server.integrations.provider import ProviderType
from openhands.app_server.services.injector import InjectorState
from openhands.sdk.llm import MetricsSnapshot, TokenUsage
from server.auth.auth_error import AuthError as SaasAuthError
from server.sharing.shared_conversation_info_service import (
    SharedConversationInfoService,
    SharedConversationInfoServiceInjector,
)
from server.sharing.shared_conversation_models import (
    SharedConversation,
)
from storage.org_member import OrgMember
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas

logger = logging.getLogger(__name__)


@dataclass
class SQLSharedConversationInfoService(SharedConversationInfoService):
    """SQL implementation of SharedConversationInfoService for shared conversations only."""

    db_session: AsyncSession
    # Authenticated caller, if any. Anonymous viewers only see public
    # conversations; org members also see their org's automation conversations.
    viewer_user_id: UUID | None = None

    async def get_shared_conversation_info(
        self, conversation_id: UUID
    ) -> SharedConversation | None:
        """Get a single conversation shared with the viewer, returning None if missing or not shared."""
        query = self._visible_select_with_saas_metadata().where(
            StoredConversationMetadata.conversation_id == str(conversation_id)
        )

        result = await self.db_session.execute(query)
        row = result.first()

        if row is None:
            return None

        stored, saas_metadata = row
        return self._to_shared_conversation(stored, saas_metadata=saas_metadata)

    def _visible_select_with_saas_metadata(self):
        """Create a select query that returns conversations visible to the viewer.

        A conversation is visible when it is public or, for an authenticated
        viewer, when it was triggered by an automation in an org the viewer is
        a member of. This joins with conversation_metadata_saas to retrieve the
        user_id needed for constructing the correct event storage path (and the
        org_id for the membership check). Uses LEFT OUTER JOIN to support
        conversations that may not have SAAS metadata (e.g., in tests).
        """
        query = (
            select(StoredConversationMetadata, StoredConversationMetadataSaas)
            .outerjoin(
                StoredConversationMetadataSaas,
                StoredConversationMetadata.conversation_id
                == StoredConversationMetadataSaas.conversation_id,
            )
            .where(StoredConversationMetadata.conversation_version == 'V1')
        )
        is_public = StoredConversationMetadata.public == True  # noqa: E712
        if self.viewer_user_id is None:
            return query.where(is_public)

        viewer_org_ids = select(OrgMember.org_id).where(
            OrgMember.user_id == self.viewer_user_id
        )
        is_org_automation = and_(
            StoredConversationMetadata.trigger == ConversationTrigger.AUTOMATION.value,
            StoredConversationMetadataSaas.org_id.in_(viewer_org_ids),
        )
        return query.where(or_(is_public, is_org_automation))

    def _to_shared_conversation(
        self,
        stored: StoredConversationMetadata,
        saas_metadata: StoredConversationMetadataSaas | None = None,
        sub_conversation_ids: list[UUID] | None = None,
    ) -> SharedConversation:
        """Convert StoredConversationMetadata to SharedConversation.

        Args:
            stored: The base conversation metadata from conversation_metadata table.
            saas_metadata: Optional SAAS metadata containing user_id and org_id.
            sub_conversation_ids: Optional list of sub-conversation IDs.
        """
        # V1 conversations should always have a sandbox_id
        sandbox_id = stored.sandbox_id
        assert sandbox_id is not None

        # Rebuild token usage
        token_usage = TokenUsage(
            prompt_tokens=stored.prompt_tokens,  # type: ignore[arg-type]
            completion_tokens=stored.completion_tokens,  # type: ignore[arg-type]
            cache_read_tokens=stored.cache_read_tokens,  # type: ignore[arg-type]
            cache_write_tokens=stored.cache_write_tokens,  # type: ignore[arg-type]
            context_window=stored.context_window,  # type: ignore[arg-type]
            per_turn_token=stored.per_turn_token,  # type: ignore[arg-type]
        )

        # Rebuild metrics object
        metrics = MetricsSnapshot(
            accumulated_cost=stored.accumulated_cost,  # type: ignore[arg-type]
            max_budget_per_task=stored.max_budget_per_task,
            accumulated_token_usage=token_usage,
        )

        # Get timestamps
        created_at = self._fix_timezone(stored.created_at)
        updated_at = self._fix_timezone(stored.last_updated_at)

        # Get user_id from SAAS metadata if available
        created_by_user_id = (
            str(saas_metadata.user_id)
            if saas_metadata and saas_metadata.user_id
            else None
        )

        return SharedConversation(
            id=UUID(stored.conversation_id),
            created_by_user_id=created_by_user_id,
            sandbox_id=stored.sandbox_id,  # type: ignore[arg-type]
            selected_repository=stored.selected_repository,
            selected_branch=stored.selected_branch,
            git_provider=(
                ProviderType(stored.git_provider) if stored.git_provider else None
            ),
            title=stored.title,
            pr_number=stored.pr_number,  # type: ignore[arg-type]
            llm_model=stored.llm_model,
            metrics=metrics,
            parent_conversation_id=(
                UUID(stored.parent_conversation_id)
                if stored.parent_conversation_id
                else None
            ),
            sub_conversation_ids=sub_conversation_ids or [],
            created_at=created_at,
            updated_at=updated_at,
        )

    def _fix_timezone(self, value: datetime | None) -> datetime:
        """Return ``value`` as an aware UTC datetime.

        A value missing its timezone is assumed to be UTC. ``None`` becomes the
        current UTC time.
        """
        if value is None:
            # Fallback for legacy data: use current time to match model defaults.
            # The DB columns have default=utc_now, so None only occurs in legacy records.
            # Using utc_now() keeps the API model non-nullable and matches new record behavior.
            return utc_now()
        if not value.tzinfo:
            value = value.replace(tzinfo=UTC)
        return value


async def resolve_viewer_user_id(
    state: InjectorState, request: Request | None
) -> UUID | None:
    """Best-effort identity of the caller of a sharing endpoint.

    Authentication is optional on the sharing endpoints. ``None`` means an
    anonymous viewer, who only sees public conversations. Any failure to
    establish the identity degrades to anonymous instead of failing the
    request, so public share links keep working and a resolution error can
    never widen access.
    """
    if request is None:
        return None
    # Define inline to prevent circular lookup
    from openhands.app_server.config import get_user_context

    try:
        async with get_user_context(state, request) as user_context:
            user_id = await user_context.get_user_id()
        return UUID(user_id) if user_id else None
    except (AppAuthError, SaasAuthError):
        # No, invalid or expired credentials.
        return None
    except Exception:
        logger.warning(
            'Could not resolve the shared-conversation viewer; '
            'serving public conversations only',
            exc_info=True,
        )
        return None


class SQLSharedConversationInfoServiceInjector(SharedConversationInfoServiceInjector):
    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[SharedConversationInfoService, None]:
        # Define inline to prevent circular lookup
        from openhands.app_server.config import get_db_session

        async with get_db_session(state, request) as db_session:
            service = SQLSharedConversationInfoService(
                db_session=db_session,
                viewer_user_id=await resolve_viewer_user_id(state, request),
            )
            yield service
