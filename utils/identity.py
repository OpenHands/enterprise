"""Shared identity helpers for resolving user profile fields from Keycloak claims."""

from pydantic import ConfigDict, TypeAdapter, with_config
from typing_extensions import TypedDict


@with_config(ConfigDict(strict=True, hide_input_in_errors=True, extra='allow'))
class UserIdentityClaims(TypedDict, total=False):
    sub: str
    name: str | None
    given_name: str | None
    family_name: str | None
    preferred_username: str
    username: str
    email: str | None
    email_verified: bool | None


IDENTITY_CLAIMS = TypeAdapter(UserIdentityClaims)


def resolve_display_name(user_info: UserIdentityClaims) -> str | None:
    """Resolve the best available display name from Keycloak user_info claims.

    Fallback chain: name → given_name + family_name → None

    Does NOT fall back to preferred_username/username — callers that need
    a guaranteed non-None value should handle that separately. This keeps
    the helper focused on real-name claims so that the /api/user/info route
    can return name=None when no real name is available, while user_store
    callers can append their own username fallback.
    """
    name = user_info.get('name', '')
    if name and name.strip():
        return name.strip()

    given = (user_info.get('given_name') or '').strip()
    family = (user_info.get('family_name') or '').strip()
    combined = f'{given} {family}'.strip()
    if combined:
        return combined

    return None
