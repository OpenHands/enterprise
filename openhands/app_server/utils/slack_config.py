import os


def is_slack_configured() -> bool:
    """Return whether Slack integration is fully configured for this instance."""
    return (
        os.getenv('SLACK_WEBHOOKS_ENABLED', 'false').lower() in ('true', '1')
        and bool(os.getenv('SLACK_CLIENT_ID', '').strip())
        and bool(os.getenv('SLACK_CLIENT_SECRET', '').strip())
        and bool(os.getenv('SLACK_SIGNING_SECRET', '').strip())
    )
