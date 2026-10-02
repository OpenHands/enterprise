from uuid import UUID

from openhands.app_server.config import get_global_config


def get_conversation_ui_url(conversation_id: UUID) -> str | None:
    """Browser URL that opens the conversation in Agent Canvas."""
    web_url = get_global_config().web_url
    if not web_url:
        return None
    return f'{web_url.rstrip("/")}/canvas/conversations/{conversation_id.hex}'
