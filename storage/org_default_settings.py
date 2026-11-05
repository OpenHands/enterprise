"""Helpers for deployment-configured organization default settings."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, TypeVar

from openhands.sdk.settings import OpenHandsAgentSettings, default_agent_settings
from server.constants import get_default_llm_base_url, get_default_llm_model
from server.org_defaults_config import get_org_defaults_condenser_config

_APPLICABLE_AGENT_KINDS = {None, 'openhands', 'llm'}
_APPLICABLE_CONDENSER_KINDS = {None, 'llm_summarizing'}
_SettingsT = TypeVar('_SettingsT', dict[str, Any], OpenHandsAgentSettings)


def _configured_max_tokens() -> int | None:
    return get_org_defaults_condenser_config().max_tokens


def create_base_org_agent_settings() -> OpenHandsAgentSettings:
    """Create default org agent settings with deployment defaults applied."""

    agent_settings = default_agent_settings()
    agent_settings.llm.model = get_default_llm_model()
    agent_settings.llm.base_url = get_default_llm_base_url()
    return apply_org_condenser_max_tokens_default(
        agent_settings,
        max_tokens=_configured_max_tokens(),
    )


def _dict_agent_kind(agent_settings: dict[str, Any]) -> str | None:
    value = agent_settings.get('agent_kind')
    return value if isinstance(value, str) else None


def _dict_condenser_kind(condenser: dict[str, Any]) -> str | None:
    value = condenser.get('condenser_kind')
    return value if isinstance(value, str) else None


def apply_org_condenser_max_tokens_default(
    agent_settings: _SettingsT,
    *,
    max_tokens: int | None = None,
    overwrite: bool = False,
) -> _SettingsT:
    """Apply deployment default ``condenser.max_tokens`` when applicable.

    Only OpenHands/legacy-LLM agent settings with the legacy/default
    LLM-summarizing condenser support this setting. ACP settings and no-op
    condensers are left unchanged.
    """

    if max_tokens is None:
        return agent_settings

    if isinstance(agent_settings, OpenHandsAgentSettings):
        model_condenser = agent_settings.condenser
        condenser_kind = getattr(model_condenser, 'condenser_kind', None)
        if condenser_kind not in _APPLICABLE_CONDENSER_KINDS:
            return agent_settings
        current = getattr(model_condenser, 'max_tokens', None)
        if overwrite or current is None:
            setattr(model_condenser, 'max_tokens', max_tokens)
        return agent_settings

    data = deepcopy(agent_settings)
    agent_kind = _dict_agent_kind(data)
    if agent_kind not in _APPLICABLE_AGENT_KINDS:
        return data

    raw_condenser = data.get('condenser')
    if raw_condenser is None:
        condenser: dict[str, Any] = {'condenser_kind': 'llm_summarizing'}
    elif isinstance(raw_condenser, dict):
        condenser = deepcopy(raw_condenser)
    else:
        condenser = {'condenser_kind': 'llm_summarizing'}

    condenser_kind = _dict_condenser_kind(condenser)
    if condenser_kind not in _APPLICABLE_CONDENSER_KINDS:
        return data

    if overwrite or condenser.get('max_tokens') is None:
        condenser['max_tokens'] = max_tokens
    data['condenser'] = condenser
    return data


def apply_configured_org_condenser_default(
    agent_settings: _SettingsT,
    *,
    overwrite: bool = False,
) -> _SettingsT:
    """Apply the configured org condenser default, if one exists."""

    return apply_org_condenser_max_tokens_default(
        agent_settings,
        max_tokens=_configured_max_tokens(),
        overwrite=overwrite,
    )


def strip_unset_condenser_max_tokens(agent_settings_diff: dict[str, Any]) -> None:
    """Remove JSON-null member ``condenser.max_tokens`` overrides.

    Legacy serialized defaults can contain ``max_tokens: null``. Treat that as
    unset so member diffs do not mask org-level deployment defaults.
    """

    condenser = agent_settings_diff.get('condenser')
    if not isinstance(condenser, dict):
        return
    if condenser.get('max_tokens') is None and 'max_tokens' in condenser:
        condenser.pop('max_tokens', None)
    if not condenser:
        agent_settings_diff.pop('condenser', None)
