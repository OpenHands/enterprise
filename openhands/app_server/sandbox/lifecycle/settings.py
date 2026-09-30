"""When the app pauses and deletes sandboxes.

Set with ``OH_SANDBOX_LIFECYCLE_*``. A value of 0 turns a rule off.
"""

from pydantic import BaseModel, Field


class SandboxLifecycleSettings(BaseModel):
    """The lifecycle settings, in seconds."""

    idle_seconds: int = Field(
        default=20 * 60,
        ge=0,
        description='Pause a sandbox once its agent has done nothing for this long.',
    )
    max_session_seconds: int = Field(
        default=12 * 60 * 60,
        ge=0,
        description=(
            'Pause a sandbox this long after it started or resumed, even if its '
            'agent is still working.'
        ),
    )
    delete_after_seconds: int = Field(
        default=10 * 24 * 60 * 60,
        ge=0,
        description=(
            'Delete a sandbox, and its workspace, once it has not run for this long.'
        ),
    )
