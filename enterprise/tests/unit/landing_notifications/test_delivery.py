import pytest
from server.services.landing_notifications.delivery import (
    DeliveryStatus,
    execute_delivery_attempts,
)
from server.services.landing_notifications.notification_models import (
    DeliveryAttempt,
    DeliveryChannel,
)

ATTEMPTS = (
    DeliveryAttempt(
        channel=DeliveryChannel.EMAIL,
        delivery_key='release:1:alice:email',
        destination='alice@openhands.dev',
        payload={
            'from': 'OpenHands <releases@openhands.dev>',
            'to': ['alice@openhands.dev'],
            'subject': 'Ready to test',
            'html': '<p>Ready</p>',
            'text': 'Ready',
        },
    ),
    DeliveryAttempt(
        channel=DeliveryChannel.SLACK,
        delivery_key='release:1:alice:slack',
        destination='U123',
        payload={'channel': 'U123', 'text': 'Ready to test', 'blocks': []},
    ),
)


def test_delivery_defaults_to_dry_run_without_credentials() -> None:
    results = execute_delivery_attempts(ATTEMPTS)

    assert [result.status for result in results] == [
        DeliveryStatus.DRY_RUN,
        DeliveryStatus.DRY_RUN,
    ]
    assert [result.delivery_key for result in results] == [
        'release:1:alice:email',
        'release:1:alice:slack',
    ]


def test_live_email_requires_resend_credential() -> None:
    with pytest.raises(ValueError, match='resend_api_key'):
        execute_delivery_attempts((ATTEMPTS[0],), dry_run=False)


def test_live_slack_requires_bot_credential() -> None:
    with pytest.raises(ValueError, match='slack_bot_token'):
        execute_delivery_attempts((ATTEMPTS[1],), dry_run=False)
