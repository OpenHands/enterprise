from datetime import datetime

from pydantic import BaseModel, Field

from openhands.agent_server.utils import utc_now

# Pod security context defaults used when the sandbox spec does not specify
# run_as_user / run_as_group / fs_group (e.g. plain SandboxSpecInfo from a
# static preset, or a warm runtime config that omits these fields). These
# match the UID/GID the agent-server image is built for.
DEFAULT_RUN_AS_USER = 10001
DEFAULT_RUN_AS_GROUP = 10001
DEFAULT_FS_GROUP = 10001


class SandboxSpecInfo(BaseModel):
    """A template for creating a Sandbox (e.g: A Docker Image vs Container)."""

    id: str
    command: list[str] | None
    created_at: datetime = Field(default_factory=utc_now)
    initial_env: dict[str, str] = Field(
        default_factory=dict, description='Initial Environment Variables'
    )
    working_dir: str = '/home/openhands/workspace'


class RemoteSandboxSpecInfo(SandboxSpecInfo):
    """A sandbox spec sourced from a remote runtime-api warm runtime config.

    Adds the pod security context fields (run_as_user / run_as_group / fs_group)
    that runtime-api may include in the warm runtime config. These are passed
    through to runtime-api when starting a sandbox so the pod runs as the same
    UID/GID that the image was built for, rather than relying on a hardcoded
    default that may not be correct for every image.
    """

    run_as_user: int = DEFAULT_RUN_AS_USER
    run_as_group: int = DEFAULT_RUN_AS_GROUP
    fs_group: int = DEFAULT_FS_GROUP


class SandboxSpecInfoPage(BaseModel):
    items: list[SandboxSpecInfo]
    next_page_id: str | None = None
