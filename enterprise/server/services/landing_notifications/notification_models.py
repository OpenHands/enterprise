from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from server.services.landing_notifications.models import Environment


class DeliveryChannel(StrEnum):
    EMAIL = 'email'
    SLACK = 'slack'


class RecipientProfile(BaseModel):
    github_login: str = Field(min_length=1)
    email: str | None = None
    slack_user_id: str | None = None
    channels: dict[Environment, frozenset[DeliveryChannel]] = Field(
        default_factory=dict
    )


class NotificationContent(BaseModel):
    event_id: str
    recipient_login: str
    subject: str
    text: str
    html: str
    slack_blocks: tuple[dict[str, Any], ...]


class DeliveryAttempt(BaseModel):
    channel: DeliveryChannel
    delivery_key: str
    destination: str
    payload: dict[str, Any]
