"""App-server discovery switch for the default OpenHands public skills.

This only stops the app server from requesting public skills. Agent-server
loading and catalog fetching are governed by the agent-server's own
environment (delivered through ``OH_AGENT_SERVER_ENV``), not by this value.
"""

import os

from pydantic import TypeAdapter, ValidationError

from openhands.app_server.utils.logger import openhands_logger as logger

PUBLIC_SKILLS_ENABLED_ENV = 'OH_PUBLIC_SKILLS_ENABLED'
_BOOL_ADAPTER = TypeAdapter(bool)


def public_skills_enabled() -> bool:
    """Unset, empty or unrecognised values keep public skills enabled."""
    raw = os.environ.get(PUBLIC_SKILLS_ENABLED_ENV, '').strip()
    if not raw:
        return True
    try:
        return _BOOL_ADAPTER.validate_python(raw)
    except ValidationError:
        logger.warning(
            'Ignoring unrecognised %s=%r; keeping it enabled',
            PUBLIC_SKILLS_ENABLED_ENV,
            raw,
        )
        return True
