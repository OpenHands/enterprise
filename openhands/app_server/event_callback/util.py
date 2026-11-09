from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import httpx

from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    SandboxInfo,
    SandboxStatus,
)
from openhands.app_server.utils.docker_utils import (
    sandbox_url_for_app_server,
)

if TYPE_CHECKING:
    from openhands.app_server.app_conversation.app_conversation_models import (
        AppConversationInfo,
    )


def ensure_conversation_found(
    app_conversation_info: AppConversationInfo | None, conversation_id: UUID
) -> AppConversationInfo:
    """Ensure conversation info exists, otherwise raise a clear error."""
    if not app_conversation_info:
        raise RuntimeError(f'Conversation not found: {conversation_id}')
    return app_conversation_info


def ensure_running_sandbox(sandbox: SandboxInfo | None, sandbox_id: str) -> SandboxInfo:
    """Ensure sandbox exists, is running, and has a session API key."""
    if not sandbox:
        raise RuntimeError(f'Sandbox not found: {sandbox_id}')

    if sandbox.status != SandboxStatus.RUNNING:
        raise RuntimeError(f'Sandbox not running: {sandbox_id}')

    if not sandbox.session_api_key:
        raise RuntimeError(f'No session API key for sandbox: {sandbox.id}')

    return sandbox


def get_agent_server_url_from_sandbox(
    sandbox: SandboxInfo, backend: str | None = None
) -> str:
    """Return the agent server URL from sandbox exposed URLs.

    ``backend`` selects the container-to-host gateway hostname
    (``host.containers.internal`` for Podman, ``host.docker.internal``
    otherwise). When omitted, the Docker default is used, preserving
    the original behavior.
    """
    exposed_urls = sandbox.exposed_urls
    if not exposed_urls:
        raise RuntimeError(f'No exposed URLs configured for sandbox {sandbox.id!r}')

    try:
        agent_server_url = next(
            exposed_url.url
            for exposed_url in exposed_urls
            if exposed_url.name == AGENT_SERVER
        )
    except StopIteration:
        raise RuntimeError(
            f'No {AGENT_SERVER!r} URL found for sandbox {sandbox.id!r}'
        ) from None

    return sandbox_url_for_app_server(agent_server_url, backend)


def agent_server_error_detail(exc: httpx.HTTPStatusError) -> str:
    """Return a readable message for an agent server error response.

    ``str(exc)`` only carries the status and URL. The agent server puts the
    real cause in ``exception`` (unhandled errors, tagged with ``error_id``)
    or ``detail`` (HTTPException).
    """
    try:
        data = exc.response.json()
    except ValueError:
        data = None
    message = None
    error_id = None
    if isinstance(data, dict):
        message = data.get('exception') or data.get('detail')
        if isinstance(message, dict):
            message = message.get('message')
        error_id = data.get('error_id')
    status = exc.response.status_code
    if not isinstance(message, str) or not message:
        return f'Agent server error: HTTP {status}'
    suffix = f'HTTP {status}'
    if error_id:
        suffix += f', error_id={error_id}'
    return f'{message} ({suffix})'
