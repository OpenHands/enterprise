"""When the app pauses and deletes sandboxes.

A backend has defaults (``OH_SANDBOX_LIFECYCLE_*``), and a sandbox spec can
override any of them (``OH_SANDBOX_SPEC_SPECS_<n>_LIFECYCLE_*``). A value of 0
turns a rule off.
"""

from pydantic import BaseModel, Field

IDLE_DESCRIPTION = 'Pause a sandbox once its agent has done nothing for this long.'
MAX_SESSION_DESCRIPTION = (
    'Pause a sandbox this long after it started or resumed, even if its agent '
    'is still working.'
)
DELETE_AFTER_DESCRIPTION = (
    'Delete a sandbox, and its workspace, once it has not run for this long.'
)


class SandboxLifecycleOverrides(BaseModel):
    """A spec's own lifecycle settings. An unset field uses the backend's."""

    idle_seconds: int | None = Field(default=None, ge=0, description=IDLE_DESCRIPTION)
    max_session_seconds: int | None = Field(
        default=None, ge=0, description=MAX_SESSION_DESCRIPTION
    )
    delete_after_seconds: int | None = Field(
        default=None, ge=0, description=DELETE_AFTER_DESCRIPTION
    )


class SandboxLifecycleSettings(BaseModel):
    """A backend's lifecycle settings, in seconds."""

    idle_seconds: int = Field(default=20 * 60, ge=0, description=IDLE_DESCRIPTION)
    max_session_seconds: int = Field(
        default=12 * 60 * 60, ge=0, description=MAX_SESSION_DESCRIPTION
    )
    delete_after_seconds: int = Field(
        default=10 * 24 * 60 * 60, ge=0, description=DELETE_AFTER_DESCRIPTION
    )

    def with_overrides(
        self, overrides: SandboxLifecycleOverrides | None
    ) -> 'SandboxLifecycleSettings':
        """These settings, with a spec's overrides applied."""
        if overrides is None:
            return self
        return self.model_copy(update=overrides.model_dump(exclude_none=True))
