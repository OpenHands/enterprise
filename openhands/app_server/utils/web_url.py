"""Resolve the public application URL without initializing application services."""

import os

from pydantic import BaseModel, Field

from openhands.agent_server.env_parser import from_env


def get_default_web_url() -> str | None:
    """Use HTTPS for the legacy WEB_HOST setting when it is present."""
    web_host = os.getenv('WEB_HOST')
    return f'https://{web_host}' if web_host else None


class _WebUrlConfig(BaseModel):
    web_url: str | None = Field(default_factory=get_default_web_url)


def get_web_url_from_env() -> str | None:
    """Read the same OH web_url field as AppServerConfig, including its fallback."""
    return _WebUrlConfig.model_validate(from_env(_WebUrlConfig, 'OH')).web_url
