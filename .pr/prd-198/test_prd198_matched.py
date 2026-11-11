"""Enterprise -> patched agent-server/SDK, same process, no network."""

import httpx
import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.app_server.app_conversation import skill_loader
from openhands.app_server.app_conversation.live_status_app_conversation_service import (
    _to_sdk_marketplace_registrations,
)
from openhands.app_server.sandbox.sandbox_spec_service import get_agent_server_env
from openhands.app_server.settings.settings_models import (
    MarketplaceRegistration,
    MarketplaceScope,
)
from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.skills.skill import _invalidate_public_skills_cache
from tests.public_extension_fixtures import (
    ORG_GITHUB,
    PLUGIN_GITHUB,
    PUBLIC_GITHUB,
    make_plugin_marketplace,
    make_public_repo,
    make_standalone_marketplace,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('EXTENSIONS_PATH', str(make_public_repo(tmp_path / 'pub')))
    for k in ('OH_PUBLIC_SKILLS_ENABLED', 'OH_PUBLIC_PLUGIN_CATALOG_ENABLED'):
        monkeypatch.delenv(k, raising=False)
    _invalidate_public_skills_cache()
    yield
    _invalidate_public_skills_cache()


def _org(tmp_path, factory, precedence):
    # Production sources are remote; local fixtures bypass the source validator.
    return [
        MarketplaceRegistration.model_construct(
            name='acme',
            source=str(factory(tmp_path / 'org')),
            ref=None,
            repo_path=None,
            auto_load=True,
            public_skill_precedence=precedence,
            scope=MarketplaceScope.ORG,
        )
    ]


def _apply_sandbox_env(monkeypatch):
    """What a cold, warm-claimed or resumed sandbox receives from Enterprise."""
    for k, v in get_agent_server_env().items():
        if k.startswith('OH_PUBLIC_'):
            monkeypatch.setenv(k, v)


async def _discover(monkeypatch, registrations, load_public):
    client = TestClient(create_app(Config(session_api_keys=[])))

    class _Async:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers=None, timeout=None):
            r = client.post('/api/skills', json=json)
            return httpx.Response(
                r.status_code, json=r.json(), request=httpx.Request('POST', url)
            )

    monkeypatch.setattr(skill_loader.httpx, 'AsyncClient', _Async)
    skills = await skill_loader.load_skills_from_agent_server(
        agent_server_url='http://sandbox',
        session_api_key=None,
        project_dir='/nonexistent',
        load_public=load_public,
        load_user=False,
        load_project=False,
        load_org=False,
        registered_marketplaces=registrations,
        load_registered_marketplaces=True,
    )
    return skills


def _by_name(skills):
    return {s.name: s.content.strip() for s in skills}


def _runtime(tmp_path, registrations, skills):
    agent = Agent(
        llm=LLM(model='test/m', api_key='k'),
        tools=[],
        agent_context=AgentContext(
            skills=skills,
            registered_marketplaces=_to_sdk_marketplace_registrations(registrations),
        ),
    )
    ws = tmp_path / 'ws'
    ws.mkdir(exist_ok=True)
    conv = LocalConversation(agent=agent, workspace=ws, visualizer=None)
    conv._ensure_plugins_loaded()
    ctx = conv.agent.agent_context
    conv.close()
    return _by_name(ctx.skills)


@pytest.mark.usefixtures('env')
@pytest.mark.parametrize(
    ('public_on', 'factory', 'precedence', 'expected'),
    [
        (True, make_standalone_marketplace, None, PUBLIC_GITHUB),
        (True, make_standalone_marketplace, 'marketplace', ORG_GITHUB),
        (True, make_plugin_marketplace, 'public', PUBLIC_GITHUB),
        (True, make_plugin_marketplace, 'marketplace', PLUGIN_GITHUB),
        (False, make_standalone_marketplace, None, ORG_GITHUB),
        (False, make_plugin_marketplace, 'public', PLUGIN_GITHUB),
    ],
)
async def test_discovery_then_runtime(
    tmp_path, monkeypatch, public_on, factory, precedence, expected
):
    monkeypatch.setenv('OH_PUBLIC_SKILLS_ENABLED', str(public_on).lower())
    _apply_sandbox_env(monkeypatch)
    regs = _org(tmp_path, factory, precedence)
    from openhands.app_server.utils.public_extension_policy import public_skills_enabled

    returned = await _discover(monkeypatch, regs, public_skills_enabled())
    discovered = _by_name(returned)
    assert discovered['github'] == expected
    github = next(s for s in returned if s.name == 'github')
    assert github.source  # provenance survives the launch handoff
    assert ('public-only' in discovered) is public_on

    # Enterprise hands the returned Skill objects (with source) to the
    # conversation as explicit skills; the runtime then activates marketplaces.
    effective = _runtime(tmp_path, regs, returned)
    assert effective['github'] == expected
    assert ('public-only' in effective) is public_on
    if not public_on:
        assert PUBLIC_GITHUB not in ''.join(effective.values())


@pytest.mark.usefixtures('env')
def test_catalog_off_on_sandbox(monkeypatch):
    monkeypatch.setenv('OH_PUBLIC_PLUGIN_CATALOG_ENABLED', '0')
    _apply_sandbox_env(monkeypatch)
    client = TestClient(create_app(Config(session_api_keys=[])))
    assert client.get('/api/plugins/marketplace').json() == {
        'plugins': [],
        'disabled': True,
    }
