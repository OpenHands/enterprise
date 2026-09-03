from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from storage.database import a_session_maker
from storage.slack_conversation import SlackConversation


@dataclass
class SlackConversationStore:
    async def get_slack_conversation(
        self, channel_id: str, parent_id: str
    ) -> SlackConversation | None:
        """Get a slack conversation by channel_id and message_ts.
        Both parameters are required to match for a conversation to be returned.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                select(SlackConversation).where(
                    SlackConversation.channel_id == channel_id,
                    SlackConversation.parent_id == parent_id,
                )
            )
            return result.scalar_one_or_none()

    async def create_slack_conversation(
        self, slack_conversation: SlackConversation
    ) -> None:
        async with a_session_maker() as session:
            await session.merge(slack_conversation)
            await session.commit()

    async def delete_slack_conversation(self, channel_id: str, parent_id: str) -> int:
        """Delete the slack conversation(s) for a channel/thread pair.

        Used to clear a mapping that points at a conversation which no longer
        exists, so the next mention in the thread starts a new conversation
        instead of repeatedly failing against the stale reference.

        Returns:
            The number of rows deleted.
        """
        async with a_session_maker() as session:
            result = await session.execute(
                delete(SlackConversation).where(
                    SlackConversation.channel_id == channel_id,
                    SlackConversation.parent_id == parent_id,
                )
            )
            await session.commit()
            return result.rowcount or 0

    @classmethod
    def get_instance(cls) -> SlackConversationStore:
        return SlackConversationStore()
