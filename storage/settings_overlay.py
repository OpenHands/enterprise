"""Translate legacy SDK payloads at the Enterprise settings read boundary."""

from typing import Any

from openhands.app_server.settings.meta_profiles import ORG_OWNED_ROUTER_SETTINGS_KEYS
from openhands.app_server.settings.scoped import resolve
from openhands.app_server.settings.settings_models import (
    normalize_member_agent_settings_diff,
)
from openhands.sdk.tool.defaults import RETIRED_TOOL_SWITCHES
from storage.org import Org
from storage.org_member import OrgMember
from storage.org_store import OrgStore


def member_settings(
    org: Org, member: OrgMember, team: dict[str, Any] | None = None
) -> dict[str, Any]:
    org_agent = OrgStore.get_agent_settings_from_org(org).model_dump(mode='json')
    member_agent = normalize_member_agent_settings_diff(
        dict(member.agent_settings_diff)
    )
    # Unversioned rows used [] for the standard tools. Do not re-run old tool
    # migrations against a parent's tools just because a member edited another field.
    if 'schema_version' not in member_agent and member_agent.get('tools') == []:
        del member_agent['tools']
    if not any(key in member_agent for key in ('tools', *RETIRED_TOOL_SWITCHES)):
        member_agent.pop('schema_version', None)
    result = resolve(
        {
            'agent_settings': org_agent,
            'conversation_settings': OrgStore.get_conversation_settings_from_org(
                org
            ).model_dump(mode='json'),
        },
        team or {},
        {
            'agent_settings': member_agent,
            'conversation_settings': member.conversation_settings_diff,
        },
    )
    # Private MCP and active router configuration have separate authorized
    # sources. Never take them from any settings layer (including old rows).
    for key in ('mcp_config', *ORG_OWNED_ROUTER_SETTINGS_KEYS):
        result['agent_settings'].pop(key, None)
    return result
