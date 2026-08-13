"""
Pydantic models for user app settings API.
"""

from pydantic import BaseModel, EmailStr, Field
from storage.user import User

# Bounds for the configurable git clone timeout (seconds). The lower bound keeps
# a clone from being killed before it can make progress; the upper bound caps how
# long a sandbox can block on startup.
MIN_GIT_CLONE_TIMEOUT_SECONDS = 30
MAX_GIT_CLONE_TIMEOUT_SECONDS = 3600


class UserAppSettingsError(Exception):
    """Base exception for user app settings errors."""

    pass


class UserNotFoundError(UserAppSettingsError):
    """Raised when user is not found."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        super().__init__(f'User with id "{user_id}" not found')


class UserAppSettingsUpdateError(UserAppSettingsError):
    """Raised when user app settings update fails."""

    pass


class UserAppSettingsResponse(BaseModel):
    """Response model for user app settings."""

    language: str | None = None
    user_consents_to_analytics: bool | None = None
    enable_sound_notifications: bool | None = None
    git_user_name: str | None = None
    git_user_email: EmailStr | None = None
    git_full_clone: bool | None = None
    git_clone_timeout: int | None = None

    @classmethod
    def from_user(cls, user: User) -> 'UserAppSettingsResponse':
        """Create response from User entity."""
        return cls(
            language=user.language,
            user_consents_to_analytics=user.user_consents_to_analytics,
            enable_sound_notifications=user.enable_sound_notifications,
            git_user_name=user.git_user_name,
            git_user_email=user.git_user_email,
            git_full_clone=user.git_full_clone,
            git_clone_timeout=user.git_clone_timeout,
        )


class UserAppSettingsUpdate(BaseModel):
    """Request model for updating user app settings (partial update)."""

    language: str | None = None
    enable_sound_notifications: bool | None = None
    git_user_name: str | None = None
    git_user_email: EmailStr | None = None
    git_full_clone: bool | None = None
    git_clone_timeout: int | None = Field(
        default=None,
        ge=MIN_GIT_CLONE_TIMEOUT_SECONDS,
        le=MAX_GIT_CLONE_TIMEOUT_SECONDS,
    )
