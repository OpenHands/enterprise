"""Read-only tasks exposed to external MCP clients under a resource-bound grant."""

from uuid import UUID

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from mcp.types import ToolAnnotations
from sqlalchemy import select

from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    StoredConversationMetadata,
)
from server.auth.external_mcp_oauth import SCOPE
from storage.database import a_session_maker
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas


def register_external_mcp_tools(mcp: FastMCP, base_url: str) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def list_my_tasks(limit: int = 10) -> list[dict[str, str | None]]:
        """List your recent OpenHands tasks from the organization you connected."""
        token = get_access_token()
        if (
            token is None
            or SCOPE not in token.scopes
            or token.resource != f'{base_url}/mcp'
            or token.subject is None
            or 'org_id' not in token.claims
        ):
            raise ToolError('OpenHands authorization is required')
        try:
            user_id = UUID(token.subject)
            org_id = UUID(token.claims['org_id'])
        except (TypeError, ValueError) as exc:
            raise ToolError('Invalid OpenHands authorization') from exc
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
                    'url': f'{base_url.removesuffix("/external-mcp")}/canvas/conversations/{conversation_id}',
                }
                for conversation_id, title in rows.all()
            ]
