"""The ACP harnesses Cloud and Replicated offer.

The SDK's ``ACP_PROVIDERS`` registry declares what *exists*; this module
declares what this deployment *offers*. Hardcoded on purpose: Replicated
inherits the list and operators cannot widen it, and registering a harness
upstream is then a no-op here.
"""

from openhands.sdk.settings import ACP_PROVIDERS

CLOUD_ACP_PROVIDERS: tuple[str, ...] = ('claude-code', 'codex', 'gemini-cli')

# ``custom`` is not a registry key — it is the sentinel meaning "the user
# supplied ``acp_command`` themselves", and it reaches an arbitrary ACP server
# regardless of this list.
ACP_CUSTOM_SERVER_KIND = 'custom'


def is_acp_provider_offered(acp_server: str | None) -> bool:
    return acp_server in (*CLOUD_ACP_PROVIDERS, ACP_CUSTOM_SERVER_KIND)


def offered_acp_providers() -> tuple[str, ...]:
    """Offered keys that the pinned SDK still registers, in declared order."""
    return tuple(key for key in CLOUD_ACP_PROVIDERS if key in ACP_PROVIDERS)
