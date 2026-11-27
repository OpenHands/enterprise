"""Self-hosted installs ship with no automatically selected LLM.

A fresh Helm install (bundled LiteLLM proxy, no ``LITELLM_DEFAULT_MODEL``) or
stock Compose install (no gateway) must not expose the built-in managed model
or the SDK placeholder as a usable default, while admin-configured install
defaults and models a member saved keep working.
"""

import pytest

from openhands.app_server.settings.llm_profiles import LLMProfiles
from openhands.sdk.llm import LLM
from openhands.sdk.settings import default_agent_settings
from server import constants
from server.verified_models.default_profile import (
    DEFAULT_LLM_PROFILE_NAME,
    materialize_default_llm_profile,
)
from server.verified_models.litellm_proxy_model_router import (
    LiteLLMProxyModelService,
)
from storage.org_default_settings import create_base_org_agent_settings

PLACEHOLDER = default_agent_settings().llm.model
PROXY_URL = 'http://oh-main-litellm.openhands.svc.cluster.local:4000'


@pytest.fixture
def helm(monkeypatch):
    """Stock chart: bundled LiteLLM proxy, LITELLM_DEFAULT_MODEL left unset."""
    monkeypatch.setattr(constants, 'DEPLOYMENT_MODE', 'self_hosted')
    monkeypatch.setattr(constants, 'ENABLE_LEGACY_LITELLM', True)
    monkeypatch.setattr(constants, 'LITE_LLM_API_URL', PROXY_URL)
    monkeypatch.setattr(constants, 'LITELLM_DEFAULT_MODEL', None)
    monkeypatch.setattr(constants, 'OPENHANDS_LLM_PROVIDER_ROUTE', None)

    async def routes(self):
        return ['team-route'], [], {}

    async def gateway_on(self):
        return True

    async def byok_off(self):
        return False

    monkeypatch.setattr(LiteLLMProxyModelService, '_fetch_proxy_model_names', routes)
    monkeypatch.setattr(LiteLLMProxyModelService, '_litellm_enabled', gateway_on)
    monkeypatch.setattr(LiteLLMProxyModelService, '_byok_enabled', byok_off)
    LiteLLMProxyModelService._reset_cache()
    yield
    LiteLLMProxyModelService._reset_cache()


@pytest.fixture
def compose(monkeypatch):
    """Stock docker-compose.yml: no gateway, no install default."""
    monkeypatch.setattr(constants, 'DEPLOYMENT_MODE', 'self_hosted')
    monkeypatch.setattr(constants, 'ENABLE_LEGACY_LITELLM', False)
    monkeypatch.setattr(constants, 'OPENHANDS_LLM_PROVIDER_ROUTE', None)

    async def gateway_off(self):
        return False

    async def byok_on(self):
        return True

    monkeypatch.setattr(LiteLLMProxyModelService, '_litellm_enabled', gateway_off)
    monkeypatch.setattr(LiteLLMProxyModelService, '_byok_enabled', byok_on)


@pytest.mark.parametrize('install', ['helm', 'compose'])
class TestFreshInstallHasNoDefault:
    async def test_no_default_model_is_advertised(self, install, request):
        request.getfixturevalue(install)

        response = await LiteLLMProxyModelService()._get_models_response()

        assert constants.has_install_default_llm_model() is False
        assert response.default_model is None

    def test_no_default_profile_is_materialized(self, install, request):
        request.getfixturevalue(install)

        profiles = materialize_default_llm_profile(LLMProfiles(), None)

        assert DEFAULT_LLM_PROFILE_NAME not in profiles.profiles
        assert profiles.active is None

    def test_new_org_holds_only_the_unconfigured_placeholder(self, install, request):
        request.getfixturevalue(install)

        llm = create_base_org_agent_settings().llm

        assert llm.model == PLACEHOLDER
        assert llm.base_url is None
        assert constants.is_unconfigured_install_llm(llm.model, llm.base_url)


class TestConfiguredAndSavedModelsAreKept:
    def test_explicit_helm_default_is_still_materialized(self, helm, monkeypatch):
        monkeypatch.setattr(
            constants, 'LITELLM_DEFAULT_MODEL', 'litellm_proxy/team-route'
        )

        profiles = materialize_default_llm_profile(LLMProfiles(), None)

        default = profiles.require(DEFAULT_LLM_PROFILE_NAME)
        assert default.model == 'openhands/team-route'
        assert default.base_url == PROXY_URL
        assert profiles.active == DEFAULT_LLM_PROFILE_NAME

    def test_saved_default_profile_is_not_removed(self, helm):
        profiles = LLMProfiles()
        profiles.save(
            DEFAULT_LLM_PROFILE_NAME,
            LLM(model='openhands/team-route', base_url=PROXY_URL),
        )
        profiles.active = DEFAULT_LLM_PROFILE_NAME

        materialize_default_llm_profile(profiles, None)

        assert profiles.require(DEFAULT_LLM_PROFILE_NAME).model == (
            'openhands/team-route'
        )
        assert profiles.active == DEFAULT_LLM_PROFILE_NAME

    @pytest.mark.parametrize(
        ('install', 'model', 'base_url', 'api_key'),
        [
            ('helm', 'openhands/team-route', PROXY_URL, 'sk-gateway'),
            ('helm', 'litellm_proxy/deepseek-v4-flash', PROXY_URL, 'sk-gateway'),
            ('helm', PLACEHOLDER, None, 'sk-own'),
            ('compose', 'openai/gpt-5.6', None, 'sk-own'),
            ('compose', PLACEHOLDER, None, 'sk-own'),
            ('compose', PLACEHOLDER, 'https://llm.example.com', None),
        ],
        ids=[
            'helm-route',
            'helm-upgraded-builtin',
            'helm-placeholder-with-own-key',
            'compose-provider-model',
            'compose-placeholder-with-own-key',
            'compose-placeholder-own-endpoint',
        ],
    )
    def test_saved_model_is_configured(
        self, install, model, base_url, api_key, request
    ):
        request.getfixturevalue(install)

        assert not constants.is_unconfigured_install_llm(model, base_url, api_key)

    @pytest.mark.parametrize('install', ['helm', 'compose'])
    def test_inferred_provider_url_does_not_make_placeholder_usable(
        self, install, request, monkeypatch
    ):
        request.getfixturevalue(install)
        provider_url = 'https://api.example.com/v1'
        monkeypatch.setattr(
            'openhands.app_server.utils.llm.get_provider_api_base',
            lambda model: provider_url,
        )

        assert constants.is_unconfigured_install_llm(PLACEHOLDER, provider_url)

    def test_direct_install_default_is_honored(self, compose, monkeypatch):
        monkeypatch.setattr(constants, 'OPENHANDS_LLM_PROVIDER_ROUTE', 'direct')
        monkeypatch.setattr(constants, 'OPENHANDS_DEFAULT_LLM_MODEL', 'openai/gpt-5')

        assert constants.has_install_default_llm_model() is True
        assert constants.get_default_llm_model() == 'openai/gpt-5'
        assert constants.is_install_llm_placeholder(PLACEHOLDER)
        assert not constants.is_unconfigured_install_llm(PLACEHOLDER)

    def test_cloud_is_unchanged(self, monkeypatch):
        monkeypatch.setattr(constants, 'DEPLOYMENT_MODE', 'cloud')
        monkeypatch.setattr(constants, 'ENABLE_LEGACY_LITELLM', True)
        monkeypatch.setattr(constants, 'LITELLM_DEFAULT_MODEL', None)
        monkeypatch.setattr(constants, 'OPENHANDS_LLM_PROVIDER_ROUTE', None)

        assert constants.has_install_default_llm_model() is True
        assert constants.get_default_llm_model() == (
            constants.get_default_litellm_model()
        )
        assert not constants.is_unconfigured_install_llm(PLACEHOLDER)
