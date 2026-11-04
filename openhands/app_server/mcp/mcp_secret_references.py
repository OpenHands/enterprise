"""``${NAME}`` secret references inside an MCP server configuration.

Conversations expand ``${NAME}`` and ``${NAME:-default}`` placeholders in a
server's URL, headers, env and auth fields from the conversation's secrets
(``openhands.sdk.skills.utils.expand_mcp_servers``). A reference without a
default that no secret satisfies is kept literally, so a server that needs it
fails to authenticate; callers use ``required`` to tell that case apart.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, NamedTuple

from openhands.sdk.mcp.config import MCPServer, dump_mcp_config

SECRET_REFERENCE = re.compile(r'\$\{([a-zA-Z_][a-zA-Z0-9_]*)(:-[^}]*)?\}')


class SecretReferences(NamedTuple):
    """Every referenced name, and the subset referenced without a default."""

    names: frozenset[str]
    required: frozenset[str]


def collect_secret_references(data: Any) -> SecretReferences:
    """Walk strings, mappings and lists for ``${NAME}`` references."""
    names: set[str] = set()
    required: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            for match in SECRET_REFERENCE.finditer(value):
                names.add(match.group(1))
                if match.group(2) is None:
                    required.add(match.group(1))
        elif isinstance(value, Mapping):
            for key, item in value.items():
                walk(key)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return SecretReferences(frozenset(names), frozenset(required))


def mcp_server_secret_references(server: MCPServer) -> SecretReferences:
    """References in ``server``, including the ones inside its credentials."""
    dump = dump_mcp_config({'server': server}, context={'expose_secrets': 'plaintext'})
    return collect_secret_references(dump['server'])
