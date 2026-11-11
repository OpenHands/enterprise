"""Instance policy for the default OpenHands public extensions repository."""

import os

# Instance policy for the default public extensions repository. The names match
# the agent-server's so one value governs discovery here and is forwarded to
# every sandbox. Unset or empty means enabled.
PUBLIC_SKILLS_ENABLED_ENV = 'OH_PUBLIC_SKILLS_ENABLED'
PUBLIC_PLUGIN_CATALOG_ENABLED_ENV = 'OH_PUBLIC_PLUGIN_CATALOG_ENABLED'


def _env_flag_enabled(name: str) -> bool:
    raw = os.environ.get(name, '').strip().lower()
    return not raw or raw in ('1', 'true', 'yes', 'on')


def public_skills_enabled() -> bool:
    return _env_flag_enabled(PUBLIC_SKILLS_ENABLED_ENV)


def public_plugin_catalog_enabled() -> bool:
    return _env_flag_enabled(PUBLIC_PLUGIN_CATALOG_ENABLED_ENV)


def public_extension_policy_env() -> dict[str, str]:
    """The normalized policy to forward to agent-server sandboxes."""
    return {
        PUBLIC_SKILLS_ENABLED_ENV: str(public_skills_enabled()).lower(),
        PUBLIC_PLUGIN_CATALOG_ENABLED_ENV: str(public_plugin_catalog_enabled()).lower(),
    }
