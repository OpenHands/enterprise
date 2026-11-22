"""Compose the legacy Enterprise JSON layers without persisting their result."""

from copy import deepcopy
from typing import Any

from openhands.app_server.utils.jsonpatch_compat import deep_merge


def overlay(base: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    """Apply the existing merge contract without aliasing either input."""
    return deepcopy(deep_merge(base, diff))


def changed_values(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Express only edits to a loaded view, not all the values it inherited."""
    result: dict[str, Any] = dict.fromkeys(before.keys() - after.keys())
    for key, value in after.items():
        if key not in before:
            result[key] = value
        elif isinstance(value, dict) and isinstance(before[key], dict):
            nested = changed_values(before[key], value)
            if nested:
                result[key] = nested
        elif before[key] != value or type(before[key]) is not type(value):
            result[key] = value
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
