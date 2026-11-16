"""Shared identity helpers for resolving user profile fields from Keycloak claims."""


def resolve_display_name(user_info: dict) -> str | None:
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

    given = user_info.get('given_name', '').strip()
    family = user_info.get('family_name', '').strip()
    combined = f'{given} {family}'.strip()
    if combined:
        return combined

    return None


def display_name_for_user(
    git_user_name: str | None,
    email: str | None,
    display_name: str | None = None,
) -> str | None:
    """Best-effort name for a stored ``User`` row.

    ``User.display_name`` comes first: service accounts have no git profile or
    email, so it is their only name. Then the git profile name, then the local
    part of the email (which is what the rest of the admin UI shows), and
    finally the email itself.
    """
    if display_name and display_name.strip():
        return display_name.strip()
    if git_user_name and git_user_name.strip():
        return git_user_name.strip()
    if email and '@' in email:
        return email.split('@', 1)[0]
    return email
