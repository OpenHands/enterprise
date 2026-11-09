from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from openhands.agent_server.utils import utc_now


class SandboxStatus(Enum):
    STARTING = 'STARTING'
    RUNNING = 'RUNNING'
    PAUSED = 'PAUSED'
    ERROR = 'ERROR'
    MISSING = 'MISSING'
    """Missing - possibly deleted"""


class SandboxStartedBy(Enum):
    """What started a sandbox, recorded on it when it starts.

    Later conversations are grouped only into sandboxes the app server
    started for a conversation. A sandbox started through the sandbox API
    belongs to the client that started it (an automation run, the SDK),
    which may delete it at any time.
    """

    APP_CONVERSATION = 'APP_CONVERSATION'
    """Started by the app server to run a conversation."""
    SANDBOX_API = 'SANDBOX_API'
    """Started by a client through ``POST /api/v1/sandboxes``."""


class ExposedUrl(BaseModel):
    """URL to access some named service within the container."""

    name: str
    url: str
    port: int


# Standard names
AGENT_SERVER = 'AGENT_SERVER'
VSCODE = 'VSCODE'
WORKER_1 = 'WORKER_1'
WORKER_2 = 'WORKER_2'


class SandboxRecord(BaseModel):
    """Persisted identity fields for a sandbox — no live runtime data.

    Contains only what is stored in the app server's own database (id,
    owner and credential organization). Use this when you need to authenticate a session key or verify
    ownership without paying for a runtime API round-trip.

    Use ``SandboxInfo`` when you additionally need live status, exposed URLs,
    or the plain-text session key returned by the runtime.
    """

    id: str
    created_by_user_id: str | None
    credential_org_id: UUID | None = None


class SandboxInfo(BaseModel):
    """Information about a sandbox."""

    id: str
    created_by_user_id: str | None
    credential_org_id: UUID | None = None
    started_by: SandboxStartedBy | None = Field(
        default=None,
        description=(
            'What started the sandbox: the app server for a conversation, or a '
            'client through the sandbox API. None for a sandbox that predates '
            'this record.'
        ),
    )
    sandbox_spec_id: str
    status: SandboxStatus
    session_api_key: str | None = Field(
        description=(
            'Key to access sandbox, to be added as an `X-Session-API-Key` header '
            'in each request. In cases where the sandbox statues is STARTING or '
            'PAUSED, or the current user does not have full access '
            'the session_api_key will be None.'
        )
    )
    exposed_urls: list[ExposedUrl] | None = Field(
        default_factory=lambda: [],
        description=(
            'URLs exposed by the sandbox (App server, Vscode, etc...)'
            'Sandboxes with a status STARTING / PAUSED / ERROR may '
            'not return urls.'
        ),
    )
    created_at: datetime = Field(default_factory=utc_now)
    status_detail: str | None = Field(
        default=None,
        description=(
            'Last pod/scheduling reason from the runtime (e.g. insufficient kvm, '
            'ImagePullBackOff), surfaced when a sandbox is stuck or errored.'
        ),
    )
    restart_count: int | None = Field(
        default=None,
        description=(
            'Number of times the sandbox container has restarted, as reported by '
            'the runtime. A value that rises between reads means the sandbox was '
            'restarted (e.g. OOM-killed) and its processes were lost. It drops '
            'back when the pod is replaced. None when unknown.'
        ),
    )
    restart_reasons: list[str] = Field(
        default_factory=list,
        description=(
            'Reason the container last terminated (e.g. OOMKilled, Error), one '
            'entry per pod. Only the most recent termination is reported.'
        ),
    )


class SandboxPage(BaseModel):
    items: list[SandboxInfo]
    next_page_id: str | None = None


class SecretNameItem(BaseModel):
    """A secret's name and optional description (value NOT included)."""

    name: str = Field(description='The secret name/key')
    description: str | None = Field(
        default=None, description='Optional description of the secret'
    )


class SecretNamesResponse(BaseModel):
    """Response listing available secret names (no raw values)."""

    secrets: list[SecretNameItem] = Field(
        default_factory=list, description='Available secrets'
    )
