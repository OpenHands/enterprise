"""The ACP harnesses this deployment surfaces.

The SDK's ``ACP_PROVIDERS`` registry declares what *exists*; this module
declares what Cloud and Replicated *offer*. Hardcoded on purpose: Replicated
inherits the list and operators cannot widen it, and registering a harness
upstream is then a no-op here — nothing below enumerates the registry, so
there is no list to keep in step with it.

The same constant name carries the same meaning in Agent Canvas
(``src/constants/acp-providers.ts``). The two are declared independently — a
client can change what it offers without the other following — and hold the
same three keys today.
"""

from openhands.app_server.errors import ACPProviderNotAvailableError
from openhands.sdk.settings import ACP_PROVIDERS, ACPAgentSettings, AgentSettingsConfig

SURFACED_ACP_PROVIDERS: tuple[str, ...] = ('claude-code', 'codex', 'gemini-cli')

# ``custom`` is not a registry key — it is the sentinel meaning "the user
# supplied ``acp_command`` themselves", and it reaches an arbitrary ACP server
# regardless of this list.
ACP_CUSTOM_SERVER_KIND = 'custom'

_REGISTERED_SURFACED_ACP_PROVIDERS = tuple(
    key for key in SURFACED_ACP_PROVIDERS if key in ACP_PROVIDERS
)


def surfaced_acp_providers() -> tuple[str, ...]:
    """Surfaced keys the pinned SDK still registers, in declared order."""
    return _REGISTERED_SURFACED_ACP_PROVIDERS


def validate_acp_provider_surfaced(agent_settings: AgentSettingsConfig | None) -> None:
    """Reject an ACP harness this deployment does not offer."""
    if not isinstance(agent_settings, ACPAgentSettings):
        return
    acp_server = agent_settings.acp_server
    if acp_server == ACP_CUSTOM_SERVER_KIND or acp_server in surfaced_acp_providers():
        return

    raise ACPProviderNotAvailableError(
        f"ACP server '{acp_server}' is not available. "
        f'Choose one of: {", ".join(surfaced_acp_providers())}.'
    )
