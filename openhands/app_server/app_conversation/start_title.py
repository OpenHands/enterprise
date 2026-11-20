import asyncio
import logging
import math
from datetime import timedelta

from openhands.agent_server.utils import utc_now
from openhands.app_server.app_conversation.app_conversation_models import (
    ConversationTitleGeneration,
)
from openhands.sdk.conversation.title_utils import (
    generate_fallback_title,
    generate_title_with_llm,
)
from openhands.sdk.llm import LLM

TITLE_DEADLINE_SECONDS = 8
_logger = logging.getLogger(__name__)


def pending_start_title(message: str) -> ConversationTitleGeneration:
    return ConversationTitleGeneration(
        status='pending',
        title=generate_fallback_title(' '.join(message.split())),
        deadline_at=utc_now() + timedelta(seconds=TITLE_DEADLINE_SECONDS),
    )


async def generate_start_title(
    pending: ConversationTitleGeneration,
    message: str,
    llm: LLM | None,
    prompt: str | None = None,
) -> ConversationTitleGeneration:
    remaining = (pending.deadline_at - utc_now()).total_seconds()
    title = None
    if llm is not None and remaining > 0:
        llm = llm.model_copy(
            update={'timeout': max(1, math.ceil(remaining)), 'num_retries': 0}
        )
        try:
            async with asyncio.timeout(remaining):
                title = await asyncio.to_thread(
                    generate_title_with_llm, message, llm, prompt=prompt
                )
        except Exception:
            _logger.info('Startup title unavailable; using first-message fallback')
    return pending.model_copy(
        update={
            'status': 'generated' if title else 'fallback',
            'title': title or pending.title,
        }
    )
