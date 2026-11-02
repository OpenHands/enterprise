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


def display_name_for_user(git_user_name: str | None, email: str | None) -> str | None:
    """Best-effort human name for a stored ``User`` row.

    ``User`` has no name column of its own -- the git profile name is the only
    real name we persist, so fall back to the local part of the email (which
    is what the rest of the admin UI shows) and finally to the email itself.
    """
    if git_user_name and git_user_name.strip():
        return git_user_name.strip()
    if email and '@' in email:
        return email.split('@', 1)[0]
    return email
