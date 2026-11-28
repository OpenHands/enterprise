import os
from enum import StrEnum


class SlackContextMode(StrEnum):
    LEGACY = 'legacy'
    MENTION_ONLY = 'mention_only'
    REQUESTER_ONLY = 'requester_only'


def get_slack_context_mode() -> SlackContextMode:
    value = os.getenv('SLACK_CONTEXT_MODE') or SlackContextMode.LEGACY
    try:
        return SlackContextMode(value)
    except ValueError:
        raise ValueError(
            'SLACK_CONTEXT_MODE must be legacy, mention_only, or requester_only'
        ) from None


def is_slack_configured() -> bool:
    """Return whether Slack integration is fully configured for this instance."""
    return (
        os.getenv('SLACK_WEBHOOKS_ENABLED', 'false').lower() in ('true', '1')
        and bool(os.getenv('SLACK_CLIENT_ID', '').strip())
        and bool(os.getenv('SLACK_CLIENT_SECRET', '').strip())
        and bool(os.getenv('SLACK_SIGNING_SECRET', '').strip())
    )
