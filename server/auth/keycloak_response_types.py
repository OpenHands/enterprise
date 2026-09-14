"""Validated identity and token responses consumed from the Keycloak client."""

from typing import NotRequired, TypeVar

from pydantic import ConfigDict, JsonValue, TypeAdapter, with_config
from typing_extensions import TypedDict

from utils.identity import UserIdentityClaims


@with_config(ConfigDict(strict=True, hide_input_in_errors=True, extra='allow'))
class KeycloakAdminUser(UserIdentityClaims, total=False):
    id: str
    enabled: bool
    attributes: dict[str, list[str]] | None
    firstName: str
    lastName: str
    emailVerified: bool
    requiredActions: list[str]


@with_config(ConfigDict(strict=True, hide_input_in_errors=True, extra='allow'))
class KeycloakRefreshTokens(TypedDict):
    access_token: str
    refresh_token: str
    expires_in: NotRequired[int]
    refresh_expires_in: NotRequired[int]
    token_type: NotRequired[str]
    id_token: NotRequired[str]
    session_state: NotRequired[str]
    scope: NotRequired[str]


ADMIN_USER = TypeAdapter(KeycloakAdminUser)
REFRESH_TOKENS = TypeAdapter(KeycloakRefreshTokens)


class KeycloakPasswordCredential(TypedDict):
    type: str
    value: str
    temporary: bool


class KeycloakUserCreation(TypedDict):
    email: str
    username: str
    enabled: bool
    emailVerified: bool
    credentials: list[KeycloakPasswordCredential]


@with_config(ConfigDict(strict=True, hide_input_in_errors=True))
class OfflineTokenResponse(TypedDict, total=False):
    refresh_token: str | None


@with_config(ConfigDict(strict=True, hide_input_in_errors=True))
class OfflineTokenEnvelope(TypedDict):
    tokens: str


ADMIN_USERS = TypeAdapter(list[KeycloakAdminUser])
USER_ID = TypeAdapter(str, config=ConfigDict(strict=True, hide_input_in_errors=True))
OFFLINE_TOKEN = TypeAdapter(OfflineTokenResponse)
OFFLINE_ENVELOPE = TypeAdapter(OfflineTokenEnvelope)

ResponseT = TypeVar('ResponseT')


def parse_keycloak_response(
    schema: TypeAdapter[ResponseT], payload: JsonValue
) -> ResponseT:
    """Validate SDK JSON without exposing credentials in validation failures."""
    try:
        return schema.validate_python(payload)
    except ValueError:
        raise ValueError('Invalid Keycloak response') from None


def parse_stored_token_envelope(payload: str) -> OfflineTokenEnvelope:
    try:
        return OFFLINE_ENVELOPE.validate_json(payload)
    except ValueError:
        raise ValueError('Invalid stored Keycloak token envelope') from None
