"""Managed defaults on self-hosted installs with a bundled LiteLLM proxy.

The chart sets ``LITELLM_DEFAULT_MODEL=litellm_proxy/<route>`` and the model
picker (``LiteLLMProxyModelService``) lists the proxy's routes as
``openhands/<route>``. New org defaults and stored bundled-proxy LLMs must use
that public name so the picker can show and select them, while keeping the
bundled proxy base_url so requests still reach it.
"""

from unittest.mock import MagicMock, patch

import pytest

from openhands.app_server.settings.llm_profiles import LLMProfiles
from openhands.sdk.llm import LLM
from openhands.sdk.llm.utils.openhands_provider import (
    OPENHANDS_LLM_PROXY_BASE_URL,
    canonicalize_openhands_llm_payload,
)
from server import constants
from server.routes import org_models
from server.routes.org_models import OrgDefaultsSettingsResponse
from server.verified_models.default_profile import (
    DEFAULT_LLM_PROFILE_NAME,
    materialize_default_llm_profile,
)
from server.verified_models.litellm_proxy_model_router import (
    LiteLLMProxyModelService,
)
from storage import org_default_settings, saas_settings_store
from storage.org import Org

with patch('storage.database.a_session_maker'):
    from server.routes import org_profiles
    from server.routes.org_profiles import _load_profiles

PROXY_ROUTES = ['claude-sonnet-4-5-20250929', 'claude-opus-4-7']
CHART_DEFAULT = 'litellm_proxy/claude-sonnet-4-5-20250929'
PROXY_URL = 'http://litellm.test:4000'


def _set_proxy_url(monkeypatch, url: str) -> None:
    for module in (constants, org_models, org_profiles, saas_settings_store):
        monkeypatch.setattr(module, 'LITE_LLM_API_URL', url)


@pytest.fixture
def bundled_proxy(monkeypatch):
    monkeypatch.setattr(constants, 'LITELLM_DEFAULT_MODEL', CHART_DEFAULT)
    monkeypatch.setattr(constants, 'DEPLOYMENT_MODE', 'self_hosted')
    monkeypatch.setattr(constants, 'OPENHANDS_LLM_PROVIDER_ROUTE', 'proxy')
    _set_proxy_url(monkeypatch, PROXY_URL)

    async def fake_fetch(self):
        return list(PROXY_ROUTES), [], {}

    async def byok_off(self):
        return False

    monkeypatch.setattr(
        LiteLLMProxyModelService, '_fetch_proxy_model_names', fake_fetch
    )
    monkeypatch.setattr(LiteLLMProxyModelService, '_byok_enabled', byok_off)
    LiteLLMProxyModelService._reset_cache()
    yield
    LiteLLMProxyModelService._reset_cache()


@pytest.fixture
def saas_proxy(monkeypatch):
    monkeypatch.setattr(constants, 'LITELLM_DEFAULT_MODEL', CHART_DEFAULT)
    monkeypatch.setattr(constants, 'DEPLOYMENT_MODE', 'cloud')
    monkeypatch.setattr(constants, 'OPENHANDS_LLM_PROVIDER_ROUTE', None)
    _set_proxy_url(monkeypatch, OPENHANDS_LLM_PROXY_BASE_URL)


async def _catalogue() -> set[str]:
    page = await LiteLLMProxyModelService().search_llm_models(limit=100)
    return {f'{m.provider}/{m.name}' for m in page.items if not m.hidden}


class TestNewOrgDefault:
    async def test_default_model_is_selectable_in_managed_catalogue(
        self, bundled_proxy
    ):
        stored = org_default_settings.create_base_org_agent_settings().llm.model
        assert stored == 'openhands/claude-sonnet-4-5-20250929'
        assert stored in await _catalogue()

    async def test_picker_query_for_default_provider_returns_models(
        self, bundled_proxy
    ):
        stored = org_default_settings.create_base_org_agent_settings().llm.model
        provider = stored.split('/', 1)[0]
        # The frontend ModelSelector issues exactly this query for the edit form.
        page = await LiteLLMProxyModelService().search_llm_models(
            provider_eq=provider, limit=100
        )
        assert {f'{m.provider}/{m.name}' for m in page.items} >= {stored}

    def test_default_keeps_bundled_proxy_base_url(self, bundled_proxy):
        llm = org_default_settings.create_base_org_agent_settings().llm
        assert llm.base_url == PROXY_URL

    def test_default_is_classified_as_managed(self, bundled_proxy):
        config = saas_settings_store.managed_llm_key_config_from_model(
            constants.get_default_llm_model(), constants.get_default_llm_base_url()
        )
        assert config is not None
        assert config.openhands_type is True

    def test_saas_default_is_unchanged(self, saas_proxy):
        assert constants.get_default_llm_model() == CHART_DEFAULT
        config = saas_settings_store.managed_llm_key_config_from_model(
            constants.get_default_llm_model(), constants.get_default_llm_base_url()
        )
        assert config is not None
        assert config.openhands_type is False

    def test_direct_provider_default_is_unchanged(self, bundled_proxy, monkeypatch):
        monkeypatch.setattr(constants, 'OPENHANDS_LLM_PROVIDER_ROUTE', 'direct')
        monkeypatch.setattr(constants, 'OPENHANDS_DEFAULT_LLM_MODEL', 'openai/gpt-5')
        monkeypatch.setattr(
            constants, 'OPENHANDS_DEFAULT_LLM_BASE_URL', 'https://llm.example.com/v1'
        )
        assert constants.get_default_llm_model() == 'openai/gpt-5'
        assert constants.get_default_llm_base_url() == 'https://llm.example.com/v1'


class TestCanonicalizeStoredLLM:
    def test_bundled_proxy_route_uses_public_name_and_keeps_base_url(
        self, bundled_proxy
    ):
        payload = {'model': CHART_DEFAULT, 'base_url': PROXY_URL + '/', 'x': 1}
        assert constants.canonicalize_bundled_proxy_llm(payload) == {
            'model': 'openhands/claude-sonnet-4-5-20250929',
            'base_url': PROXY_URL + '/',
            'x': 1,
        }

    @pytest.mark.parametrize(
        'payload',
        [
            {'model': CHART_DEFAULT, 'base_url': 'https://byok.example.com'},
            {'model': CHART_DEFAULT, 'base_url': None},
            {'model': 'anthropic/claude-sonnet-4-5', 'base_url': PROXY_URL},
            {'model': 'openhands/claude-opus-4-7', 'base_url': PROXY_URL},
        ],
    )
    def test_other_llms_are_untouched(self, bundled_proxy, payload):
        assert constants.canonicalize_bundled_proxy_llm(payload) == payload

    def test_saas_canonicalization_is_unchanged(self, saas_proxy):
        payload = {
            'model': 'litellm_proxy/claude-opus-4-8',
            'base_url': OPENHANDS_LLM_PROXY_BASE_URL,
        }
        assert constants.canonicalize_bundled_proxy_llm(payload) == payload
        assert constants.canonicalize_bundled_proxy_llm(
            canonicalize_openhands_llm_payload(payload)
        ) == {'model': 'openhands/claude-opus-4-8'}


def _org(llm_profiles=None, agent_settings=None) -> Org:
    org = MagicMock(spec=Org)
    org.llm_profiles = llm_profiles
    org.agent_settings = agent_settings or {}
    return org


class TestStoredProfiles:
    def test_load_profiles_represents_bundled_default_as_openhands(self, bundled_proxy):
        org = _org(
            {
                'profiles': {
                    'Default': {'model': CHART_DEFAULT, 'base_url': PROXY_URL},
                    'byok': {
                        'model': 'litellm_proxy/my-model',
                        'base_url': 'https://byok.example.com',
                    },
                },
                'active': 'Default',
            }
        )
        profiles = _load_profiles(org)
        default = profiles.require('Default')
        assert default.model == 'openhands/claude-sonnet-4-5-20250929'
        assert default.base_url == PROXY_URL
        byok = profiles.require('byok')
        assert byok.model == 'litellm_proxy/my-model'
        assert byok.base_url == 'https://byok.example.com'

    def test_bundled_default_survives_without_db_default(self, bundled_proxy):
        profiles = LLMProfiles()
        profiles.save(
            DEFAULT_LLM_PROFILE_NAME,
            LLM(model='openhands/claude-sonnet-4-5-20250929', base_url=PROXY_URL),
        )
        profiles.active = DEFAULT_LLM_PROFILE_NAME
        materialize_default_llm_profile(profiles, None)
        assert profiles.active == DEFAULT_LLM_PROFILE_NAME
        assert (
            profiles.require(DEFAULT_LLM_PROFILE_NAME).model
            == 'openhands/claude-sonnet-4-5-20250929'
        )

    def test_default_pointer_without_db_default_is_still_dropped(self, bundled_proxy):
        profiles = LLMProfiles()
        profiles.save(DEFAULT_LLM_PROFILE_NAME, LLM(model='openhands/stale'))
        profiles.active = DEFAULT_LLM_PROFILE_NAME
        materialize_default_llm_profile(profiles, None)
        assert profiles.get(DEFAULT_LLM_PROFILE_NAME) is None
        assert profiles.active is None

    def test_org_defaults_response_shows_openhands_name(self, bundled_proxy):
        org = _org(
            agent_settings={'llm': {'model': CHART_DEFAULT, 'base_url': PROXY_URL}}
        )
        org.conversation_settings = {}
        org.llm_api_key = None
        org.search_api_key = None
        response = OrgDefaultsSettingsResponse.from_org(org)
        assert response.agent_settings.llm.model == (
            'openhands/claude-sonnet-4-5-20250929'
        )
