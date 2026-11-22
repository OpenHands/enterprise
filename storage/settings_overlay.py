"""Compose the legacy Enterprise JSON layers without persisting their result."""

from copy import deepcopy
from typing import Any

from openhands.app_server.utils.jsonpatch_compat import deep_merge


def overlay(base: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    """Apply the existing merge contract without aliasing either input."""
    return deepcopy(deep_merge(base, diff))


def edit_scope(local: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Edit one sparse document. A legacy null resets the local path, even
    when none of its ancestors have a local override yet.
    """
    result = deepcopy(local)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            prior = result.get(key)
            nested = edit_scope(prior if isinstance(prior, dict) else {}, value)
            if nested:
                result[key] = nested
            else:
                result.pop(key, None)
        else:
            result[key] = deepcopy(value)
    return result


def compose_settings(
    org_agent: dict[str, Any],
    org_conversation: dict[str, Any],
    member_agent: dict[str, Any],
    member_conversation: dict[str, Any],
) -> dict[str, Any]:
    return overlay(
        {'agent_settings': org_agent, 'conversation_settings': org_conversation},
        {'agent_settings': member_agent, 'conversation_settings': member_conversation},
    )
