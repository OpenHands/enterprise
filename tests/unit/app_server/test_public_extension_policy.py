"""PRD-198: instance public-extensions policy and org conflict preference."""

import pytest

from openhands.app_server.app_conversation.live_status_app_conversation_service import (
    _to_sdk_marketplace_registrations,
)
from openhands.app_server.sandbox.sandbox_spec_service import get_agent_server_env
from openhands.app_server.settings.marketplace_composition import compose_marketplaces
from openhands.app_server.settings.settings_models import MarketplaceRegistration
from openhands.app_server.user import skills_router
from openhands.app_server.utils.public_extension_policy import (
    PUBLIC_PLUGIN_CATALOG_ENABLED_ENV,
    PUBLIC_SKILLS_ENABLED_ENV,
    public_extension_policy_env,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for env in (PUBLIC_SKILLS_ENABLED_ENV, PUBLIC_PLUGIN_CATALOG_ENABLED_ENV):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv('OH_AGENT_SERVER_ENV', raising=False)


def test_defaults_are_enabled():
    assert public_extension_policy_env() == {
        PUBLIC_SKILLS_ENABLED_ENV: 'true',
        PUBLIC_PLUGIN_CATALOG_ENABLED_ENV: 'true',
    }


@pytest.mark.parametrize('value', ['false', '0', 'off'])
def test_controls_are_independent(monkeypatch, value):
    monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, value)
    assert public_extension_policy_env() == {
        PUBLIC_SKILLS_ENABLED_ENV: 'false',
        PUBLIC_PLUGIN_CATALOG_ENABLED_ENV: 'true',
    }


@pytest.mark.parametrize('value', ['1', 'true'])
def test_truthy_values(monkeypatch, value):
    monkeypatch.setenv(PUBLIC_PLUGIN_CATALOG_ENABLED_ENV, value)
    assert public_extension_policy_env()[PUBLIC_PLUGIN_CATALOG_ENABLED_ENV] == 'true'


def test_sandbox_env_carries_policy_and_overrides_cannot_reenable(monkeypatch):
    monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, 'false')
    monkeypatch.setenv(PUBLIC_PLUGIN_CATALOG_ENABLED_ENV, 'false')
    monkeypatch.setenv(
        'OH_AGENT_SERVER_ENV',
        f'{{"{PUBLIC_SKILLS_ENABLED_ENV}": "true", "OTHER": "x"}}',
    )
    env = get_agent_server_env()
    assert env[PUBLIC_SKILLS_ENABLED_ENV] == 'false'
    assert env[PUBLIC_PLUGIN_CATALOG_ENABLED_ENV] == 'false'
    assert env['OTHER'] == 'x'


def test_precedence_kept_for_org_and_stripped_for_personal():
    composed = compose_marketplaces(
        instance_marketplaces=[],
        org_marketplaces=[
            {
                'name': 'org',
                'source': 'github:acme/org',
                'auto_load': True,
                'public_skill_precedence': 'marketplace',
            }
        ],
        user_marketplaces=[
            {
                'name': 'mine',
                'source': 'github:me/mine',
                'public_skill_precedence': 'marketplace',
            }
        ],
    )
    assert composed.inherited[0].public_skill_precedence == 'marketplace'
    assert composed.personal[0].public_skill_precedence is None


def test_precedence_reaches_sdk_registration():
    registration = MarketplaceRegistration(
        name='org',
        source='github:acme/org',
        auto_load=True,
        public_skill_precedence='marketplace',
    )
    (sdk,) = _to_sdk_marketplace_registrations([registration])
    assert sdk.name == 'org' and sdk.auto_load is True
    assert getattr(sdk, 'public_skill_precedence', 'marketplace') == 'marketplace'


async def test_global_skill_listing_respects_policy(monkeypatch):
    called = False

    def _load():
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(skills_router, 'load_public_skills', _load)
    monkeypatch.setenv(PUBLIC_SKILLS_ENABLED_ENV, 'false')
    assert await skills_router._load_global_skills() == []
    assert called is False
