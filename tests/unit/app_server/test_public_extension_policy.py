"""App-server switch for default public OpenHands skills."""

import pytest

from openhands.app_server.sandbox.sandbox_spec_service import get_agent_server_env
from openhands.app_server.user import skills_router
from openhands.app_server.utils.public_extension_policy import (
    PUBLIC_SKILLS_ENABLED_ENV,
    public_skills_enabled,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(PUBLIC_SKILLS_ENABLED_ENV, raising=False)
    monkeypatch.delenv('OH_AGENT_SERVER_ENV', raising=False)


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        (None, True),
        ('', True),
        ('true', True),
        ('1', True),
        ('false', False),
        ('0', False),
        ('off', False),
        ('enabled', True),
        ('tru', True),
    ],
)
def test_switch_values(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, value)
    assert public_skills_enabled() is expected


def test_app_setting_is_not_forwarded_to_sandbox(monkeypatch):
    monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, 'false')
    assert PUBLIC_SKILLS_ENABLED_ENV not in get_agent_server_env()


def test_agent_server_policy_arrives_through_explicit_env(monkeypatch):
    monkeypatch.setenv(
        'OH_AGENT_SERVER_ENV',
        '{"OH_PUBLIC_SKILLS_ENABLED": "false",'
        ' "OH_PUBLIC_PLUGIN_CATALOG_ENABLED": "false"}',
    )
    env = get_agent_server_env()
    assert env['OH_PUBLIC_SKILLS_ENABLED'] == 'false'
    assert env['OH_PUBLIC_PLUGIN_CATALOG_ENABLED'] == 'false'


async def test_global_skill_listing_respects_switch(monkeypatch):
    called = False

    def _load():
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(skills_router, 'load_public_skills', _load)
    monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, 'false')
    assert await skills_router._load_global_skills() == []
    assert called is False
