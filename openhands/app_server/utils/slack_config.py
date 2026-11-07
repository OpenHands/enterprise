import os


def is_slack_deployment_enabled() -> bool:
    """Return whether this instance has Slack webhooks on and a signing secret."""
    return os.getenv('SLACK_WEBHOOKS_ENABLED', 'false').lower() in (
        'true',
        '1',
    ) and bool(os.getenv('SLACK_SIGNING_SECRET', '').strip())


def is_slack_configured() -> bool:
    """Return whether Slack integration is fully configured by environment variables."""
    return (
        is_slack_deployment_enabled()
        and bool(os.getenv('SLACK_CLIENT_ID', '').strip())
        and bool(os.getenv('SLACK_CLIENT_SECRET', '').strip())
    )
