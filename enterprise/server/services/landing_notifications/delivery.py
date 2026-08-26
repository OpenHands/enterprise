from enum import StrEnum
from typing import Any, cast

import resend
from pydantic import BaseModel
from resend.emails._emails import Emails
from server.services.landing_notifications.notification_models import (
    DeliveryAttempt,
    DeliveryChannel,
)
from slack_sdk import WebClient


class DeliveryStatus(StrEnum):
    DRY_RUN = 'dry-run'
    SENT = 'sent'


class DeliveryResult(BaseModel):
    delivery_key: str
    channel: DeliveryChannel
    status: DeliveryStatus
    provider_message_id: str | None = None


def execute_delivery_attempts(
    attempts: tuple[DeliveryAttempt, ...],
    *,
    dry_run: bool = True,
    resend_api_key: str | None = None,
    slack_bot_token: str | None = None,
) -> tuple[DeliveryResult, ...]:
    if dry_run:
        return tuple(
            DeliveryResult(
                delivery_key=attempt.delivery_key,
                channel=attempt.channel,
                status=DeliveryStatus.DRY_RUN,
            )
            for attempt in attempts
        )

    slack_client: WebClient | None = None
    results: list[DeliveryResult] = []
    for attempt in attempts:
        if attempt.channel == DeliveryChannel.EMAIL:
            if not resend_api_key:
                raise ValueError('resend_api_key is required for email delivery')
            results.append(_send_email(attempt, resend_api_key))
            continue

        if not slack_bot_token:
            raise ValueError('slack_bot_token is required for Slack delivery')
        if slack_client is None:
            slack_client = WebClient(token=slack_bot_token)
        results.append(_send_slack(attempt, slack_client))
    return tuple(results)


def _send_email(attempt: DeliveryAttempt, api_key: str) -> DeliveryResult:
    resend.api_key = api_key
    params = cast(Emails.SendParams, attempt.payload)
    response = resend.Emails.send(
        params,
        options={'idempotency_key': attempt.delivery_key},
    )
    return DeliveryResult(
        delivery_key=attempt.delivery_key,
        channel=attempt.channel,
        status=DeliveryStatus.SENT,
        provider_message_id=response.get('id'),
    )


def _send_slack(attempt: DeliveryAttempt, client: WebClient) -> DeliveryResult:
    payload = attempt.payload
    response = client.chat_postMessage(
        channel=str(payload['channel']),
        text=str(payload['text']),
        blocks=cast(list[dict[str, Any]], payload['blocks']),
        client_msg_id=attempt.delivery_key,
        unfurl_links=False,
        unfurl_media=False,
    )
    return DeliveryResult(
        delivery_key=attempt.delivery_key,
        channel=attempt.channel,
        status=DeliveryStatus.SENT,
        provider_message_id=response.get('ts'),
    )
