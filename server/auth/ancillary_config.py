"""Reject enabled integrations whose account-link flow requires Keycloak."""

import os

from server.auth.auth_config import ENABLE_KEYCLOAK


def validate_native_ancillary_config() -> None:
    if ENABLE_KEYCLOAK:
        return
    if os.getenv('ENABLE_LINEAR', 'false').lower() in ('true', '1'):
        raise ValueError(
            'Linear account linking is unavailable in native authentication mode; disable the Linear integration'
        )
    slack_enabled = os.getenv('SLACK_WEBHOOKS_ENABLED', 'false').lower() in (
        'true',
        '1',
    )
    if slack_enabled or any(
        os.getenv(name)
        for name in ('SLACK_CLIENT_ID', 'SLACK_CLIENT_SECRET', 'SLACK_SIGNING_SECRET')
    ):
        raise ValueError(
            'Slack account linking is unavailable in native authentication mode; disable the Slack integration'
        )
