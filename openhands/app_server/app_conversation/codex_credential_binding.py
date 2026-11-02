"""Keep a sandbox's Codex credential in sync with the saved ``CODEX_AUTH_JSON``.

Codex rotates the ChatGPT refresh token in ``auth.json`` on every refresh, and
a consumed refresh token cannot be reused. The agent server's Codex credential
lifecycle keeps the sandbox's copy private and writes every rotation back
through a versioned credential binding, but only once a binding is activated.
Without one the rotation dies with the sandbox and the next sandbox starts
from the saved, already consumed token (OHE-3289).

This module activates the agent server's HTTP binding against the app
server's sandbox-scoped callback in ``sandbox_router``: before a conversation
is created (the agent server consumes the pending binding at create and strips
the plaintext copy) and again after a sandbox resume, when the agent server
refuses to hydrate a bound conversation until the binding is re-sent with the
sandbox's new session key.
"""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID

import httpx
from pydantic import BaseModel, Field

from openhands.agent_server.models import ConversationPage
from openhands.app_server.constants import MAX_API_SECRET_VALUE_LENGTH
from openhands.app_server.sandbox.sandbox_models import SandboxInfo
from openhands.sdk.agent import ACPAgent
from openhands.sdk.agent.acp_file_credentials import CODEX_AUTH_SECRET_NAME

_logger = logging.getLogger(__name__)

CODEX_ACP_SERVER = 'codex'
_ACTIVATION_TIMEOUT_SECONDS = 30.0
_SEARCH_PAGE_SIZE = 100


class CredentialBindingValue(BaseModel):
    """What the agent server loads from the callback."""

    value: str
    version: str


class CredentialBindingReplacement(BaseModel):
    """What the agent server sends to the callback after a rotation."""

    expected_version: str = Field(min_length=1)
    value: str = Field(min_length=1, max_length=MAX_API_SECRET_VALUE_LENGTH)


class CredentialBindingVersion(BaseModel):
    version: str


def credential_version(value: str) -> str:
    """Opaque version of a saved credential value.

    A digest of the value itself, so re-saving the same value (the settings UI
    rewrites every personal secret on any edit) keeps the version a running
    sandbox holds, while a rotation changes it.
    """
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def credential_binding_callback_url(web_url: str, sandbox_id: str) -> str:
    return (
        f'{web_url.rstrip("/")}/api/v1/sandboxes/{sandbox_id}'
        f'/credential-bindings/{CODEX_AUTH_SECRET_NAME}'
    )


async def activate_codex_credential_binding(
    httpx_client: httpx.AsyncClient,
    *,
    agent_server_url: str,
    session_api_key: str,
    callback_url: str,
    conversation_id: UUID,
) -> bool:
    """Point one conversation's Codex credential at the canonical callback.

    Before the conversation exists the agent server keeps the binding pending
    and consumes it at create; afterwards it re-authorizes a bound conversation
    and refuses (409) one already running on a plaintext copy. The agent server
    probes the callback before accepting, so a user without a personal
    ``CODEX_AUTH_JSON`` is refused here (422) and keeps today's static copy.
    Returns whether the binding was accepted; a refusal is logged, never
    raised, so neither a conversation start nor a resume is blocked on it.
    """
    try:
        response = await httpx_client.put(
            f'{agent_server_url.rstrip("/")}/api/conversations/{conversation_id}'
            f'/credential-bindings/{CODEX_AUTH_SECRET_NAME}',
            json={
                'url': callback_url,
                'headers': {'X-Session-API-Key': session_api_key},
            },
            headers={'X-Session-API-Key': session_api_key},
            timeout=_ACTIVATION_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        _logger.warning(
            'codex_credential_binding:activation_failed',
            extra={'conversation_id': str(conversation_id)},
            exc_info=True,
        )
        return False
    _logger.info(
        'codex_credential_binding:activated',
        extra={'conversation_id': str(conversation_id)},
    )
    return True


async def reactivate_sandbox_codex_credential_bindings(
    httpx_client: httpx.AsyncClient,
    *,
    sandbox: SandboxInfo,
    agent_server_url: str,
    web_url: str,
) -> None:
    """Re-send the binding for every Codex conversation on a resumed sandbox.

    Once bound, a conversation's plaintext credential is scrubbed and the agent
    server refuses to hydrate it after a restart (REST 409, WebSocket 1013)
    until the binding is re-sent with the sandbox's current session key. The
    agent server's own catalog says which conversations it hosts; the catalog
    is served cold, so listing it does not trip that guard. Best effort:
    failures are logged, never raised.
    """
    if not sandbox.session_api_key:
        return
    callback_url = credential_binding_callback_url(web_url, sandbox.id)
    headers = {'X-Session-API-Key': sandbox.session_api_key}
    try:
        page_id: str | None = None
        while True:
            response = await httpx_client.get(
                f'{agent_server_url.rstrip("/")}/api/conversations/search',
                params={'limit': _SEARCH_PAGE_SIZE}
                | ({'page_id': page_id} if page_id else {}),
                headers=headers,
                timeout=_ACTIVATION_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            page = ConversationPage.model_validate(response.json())
            for info in page.items:
                agent = info.agent
                if not (
                    isinstance(agent, ACPAgent) and agent.acp_server == CODEX_ACP_SERVER
                ):
                    continue
                await activate_codex_credential_binding(
                    httpx_client,
                    agent_server_url=agent_server_url,
                    session_api_key=sandbox.session_api_key,
                    callback_url=callback_url,
                    conversation_id=info.id,
                )
            if page.next_page_id is None:
                return
            page_id = page.next_page_id
    except Exception:
        _logger.warning(
            'codex_credential_binding:reactivation_failed',
            extra={'sandbox_id': sandbox.id},
            exc_info=True,
        )
