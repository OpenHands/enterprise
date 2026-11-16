from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlparse

from integrations.jira_dc.jira_dc_auth_provider import (
    JiraDcSecretHint,
    get_jira_dc_secret_hint,
)
from integrations.jira_dc.jira_dc_user_token import (
    JiraDcUserToken,
    JiraDcUserTokenError,
    get_user_jira_dc_token,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    ConversationTrigger,
)
from openhands.app_server.app_conversation.conversation_secret_enricher import (
    ConversationSecretEnricher,
    ConversationSecretEnrichment,
)
from openhands.app_server.services.jwt_service import JwtAudience, JwtService
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.user.user_models import UserInfo
from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.sdk.secret import LookupSecret, SecretSource, StaticSecret
from server.auth.constants import JIRA_DC_BASE_URL, JIRA_DC_ENABLE_OAUTH
from server.auth.token_manager import TokenManager
from storage.jira_dc_integration_store import (
    JiraDcIntegrationStore,
    workspace_available_install_wide,
)


def _jira_dc_secret_hint(base_url: str, secret_name: str) -> str:
    # The base URL is deliberately spelled out (not exposed as a secret/env var):
    # registered secrets get masked as <secret-hidden> in agent output, which
    # mangled every Jira link the agent echoed back to the user.
    return f"""You have credentialed access to the Jira Data Center REST API:

  - Base URL: {base_url}
  - Auth:     Authorization: Bearer ${secret_name}

The token value is supplied only as a sandbox secret/environment variable. Never echo or log the value of ${secret_name}."""


def _email_mode_hint(host: str) -> str:
    # Hedged on purpose: the user may have their own Jira token/MCP tools, so tell
    # the agent to use those if present and only otherwise list the enablement
    # options -- don't assert "no access", which would be wrong in that case.
    # `host` comes from the active workspace, not JIRA_DC_BASE_URL, which the
    # chart only sets in OAuth mode.
    return (
        f'Jira Data Center for this workspace is at {host}, but the admin has '
        'enabled email-linking mode so no Jira token is automatically provided '
        'to conversations. If the user asks you to read or query Jira Data '
        'Center, check to see if you have an available Jira token (e.g. a Secret) '
        'or Jira MCP tools in your environment; otherwise, tell the user how to '
        'enable access: an admin can enable OAuth mode in the OHE Admin Config '
        'and redeploy, or the user can add their Jira API token as a Secret, or '
        'connect a Jira Data Center MCP server (Settings → MCP).'
    )


def _append_email_mode_hint(system_message_suffix: str | None, host: str) -> str:
    hint = _email_mode_hint(host)
    if not system_message_suffix:
        return hint
    if 'Jira Data Center MCP server' in system_message_suffix:
        return system_message_suffix
    return f'{system_message_suffix}\n\n{hint}'


def _workspace_matches_configured_jira_dc_host(workspace_name: str) -> bool:
    configured_host = urlparse(JIRA_DC_BASE_URL).hostname
    if not configured_host:
        return False
    return workspace_name.lower() == configured_host.lower()


def _append_jira_dc_hint(
    system_message_suffix: str | None, base_url: str, secret_name: str
) -> str:
    hint = _jira_dc_secret_hint(base_url, secret_name)
    if not system_message_suffix:
        return hint
    if secret_name in system_message_suffix:
        return system_message_suffix
    return f'{system_message_suffix}\n\n{hint}'


def _auth_provider_hint_enrichment(
    system_message_suffix: str | None, hint: JiraDcSecretHint
) -> ConversationSecretEnrichment:
    return ConversationSecretEnrichment(
        system_message_suffix=_append_jira_dc_hint(
            system_message_suffix, hint.base_url, hint.secret_name
        )
    )


async def _without_integration_token(
    user_id: str, system_message_suffix: str | None
) -> ConversationSecretEnrichment:
    # The generic named-secret path may still expose the user's token from a
    # ``jira_dc`` auth provider; tell the agent how to use it.
    hint = await get_jira_dc_secret_hint(user_id)
    if hint is None:
        return ConversationSecretEnrichment(system_message_suffix=system_message_suffix)
    return _auth_provider_hint_enrichment(system_message_suffix, hint)


async def _resolve_user_token(
    *,
    user_id: str,
    workspace_id: int,
    token_manager: TokenManager,
    store: JiraDcIntegrationStore,
    strict: bool,
) -> JiraDcUserToken | None:
    try:
        return await get_user_jira_dc_token(
            keycloak_user_id=user_id,
            workspace_id=workspace_id,
            token_manager=token_manager,
            store=store,
        )
    except JiraDcUserTokenError:
        if strict:
            raise
        logger.warning(
            '[Jira DC] Skipping Jira DC token injection because the linked user '
            'does not have a usable token',
            exc_info=True,
        )
        return None


class JiraDcConversationSecretEnricher(ConversationSecretEnricher):
    async def enrich(
        self,
        *,
        user_context: UserContext,
        user: UserInfo,
        trigger: ConversationTrigger | None,
        system_message_suffix: str | None,
        web_url: str | None,
        jwt_service: JwtService,
        access_token_hard_timeout: timedelta | None,
    ) -> ConversationSecretEnrichment:
        # Identity is resolved via user_context; `user` is required only by the interface.
        del user

        user_id = await user_context.get_user_id()

        if not JIRA_DC_ENABLE_OAUTH:
            # Email mode: no per-user token to inject. A token from a jira_dc auth
            # provider may still be exposed; otherwise, when a connection is set
            # up on the install, tell the agent how to point the user at enabling
            # live access instead of failing silently.
            hint = await get_jira_dc_secret_hint(user_id) if user_id else None
            if hint:
                return _auth_provider_hint_enrichment(system_message_suffix, hint)
            store = JiraDcIntegrationStore.get_instance()
            workspace = await store.get_active_workspace()
            if workspace and workspace_available_install_wide(workspace):
                return ConversationSecretEnrichment(
                    system_message_suffix=_append_email_mode_hint(
                        system_message_suffix, workspace.name
                    )
                )
            return ConversationSecretEnrichment(
                system_message_suffix=system_message_suffix
            )

        if not user_id:
            return ConversationSecretEnrichment(
                system_message_suffix=system_message_suffix
            )

        store = JiraDcIntegrationStore.get_instance()
        jira_dc_user = await store.get_user_by_active_workspace(user_id)
        if not jira_dc_user:
            return await _without_integration_token(user_id, system_message_suffix)

        workspace = await store.get_workspace_by_id(jira_dc_user.jira_dc_workspace_id)
        if (
            not workspace
            or not workspace_available_install_wide(workspace)
            or not _workspace_matches_configured_jira_dc_host(workspace.name)
        ):
            return await _without_integration_token(user_id, system_message_suffix)

        secrets: dict[str, SecretSource] = {}

        token_manager = TokenManager()
        strict = trigger == ConversationTrigger.JIRA
        if web_url:
            if strict:
                # Jira resolver starts must fail before launching the sandbox so
                # the webhook manager can post the existing re-link prompt.
                await _resolve_user_token(
                    user_id=user_id,
                    workspace_id=workspace.id,
                    token_manager=token_manager,
                    store=store,
                    strict=True,
                )
            access_token = jwt_service.create_jws_token(
                payload={
                    'user_id': user_id,
                    'integration': 'jira_dc',
                    'secret_name': 'JIRA_DC_TOKEN',
                    'workspace_id': workspace.id,
                },
                expires_in=access_token_hard_timeout,
                audience=JwtAudience.JIRA_DC_SECRET,
            )
            secrets['JIRA_DC_TOKEN'] = LookupSecret(
                url=f'{web_url}/integration/jira-dc/secrets/token',
                headers={'X-Access-Token': access_token},
                description='Jira Data Center OAuth access token',
            )
        else:
            user_token = await _resolve_user_token(
                user_id=user_id,
                workspace_id=workspace.id,
                token_manager=token_manager,
                store=store,
                strict=strict,
            )
            if user_token is None:
                return await _without_integration_token(user_id, system_message_suffix)
            secrets['JIRA_DC_TOKEN'] = StaticSecret(
                value=user_token.access_token,
                description='Jira Data Center OAuth access token',
            )

        return ConversationSecretEnrichment(
            secrets=secrets,
            system_message_suffix=_append_jira_dc_hint(
                system_message_suffix, JIRA_DC_BASE_URL, 'JIRA_DC_TOKEN'
            ),
        )
