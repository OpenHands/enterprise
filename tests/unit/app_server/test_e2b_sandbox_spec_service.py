"""Tests for the E2B sandbox spec service.

Covers the defaults the backend ships with, and the secrecy requirement that
makes it safe to carry the template's init key on the spec: the key must never
reach the public ``GET /api/v1/sandbox-specs/search`` payload.
"""

from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from openhands.app_server.sandbox.e2b_sandbox_spec_service import (
    DEFAULT_AGENT_SERVER_PORT,
    DEFAULT_TEMPLATE_NAME,
    DEFAULT_VSCODE_PORT,
    DEFAULT_WORKING_DIR,
    E2BSandboxSpecInfo,
    E2BSandboxSpecServiceInjector,
    get_default_sandbox_specs,
)
from openhands.app_server.sandbox.preset_sandbox_spec_service import (
    PresetSandboxSpecService,
)
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfoPage

INIT_API_KEY = 'super-secret-init-key'


@pytest.fixture
def spec() -> E2BSandboxSpecInfo:
    return E2BSandboxSpecInfo(
        id=DEFAULT_TEMPLATE_NAME,
        command=None,
        working_dir=DEFAULT_WORKING_DIR,
        init_api_key=SecretStr(INIT_API_KEY),
    )


class TestDefaults:
    def test_default_spec(self, monkeypatch):
        monkeypatch.delenv('E2B_TEMPLATE', raising=False)
        monkeypatch.delenv('E2B_INIT_API_KEY', raising=False)

        specs = get_default_sandbox_specs()

        assert len(specs) == 1
        assert specs[0].id == DEFAULT_TEMPLATE_NAME
        assert specs[0].working_dir == DEFAULT_WORKING_DIR
        assert specs[0].command is None
        assert specs[0].init_api_key is None
        assert specs[0].agent_server_port == DEFAULT_AGENT_SERVER_PORT
        assert specs[0].vscode_port == DEFAULT_VSCODE_PORT

    def test_default_spec_from_env(self, monkeypatch):
        monkeypatch.setenv('E2B_TEMPLATE', 'my-template')
        monkeypatch.setenv('E2B_INIT_API_KEY', INIT_API_KEY)

        specs = get_default_sandbox_specs()

        assert specs[0].id == 'my-template'
        assert specs[0].init_api_key is not None
        assert specs[0].init_api_key.get_secret_value() == INIT_API_KEY

    def test_initial_env_is_empty(self, monkeypatch):
        """Nothing is baked into the spec: it is published verbatim."""
        monkeypatch.setenv('LLM_API_KEY', 'sk-do-not-publish-this')

        assert get_default_sandbox_specs()[0].initial_env == {}


class TestInjector:
    @pytest.mark.asyncio
    async def test_yields_preset_service_with_specs(self, spec):
        injector = E2BSandboxSpecServiceInjector(specs=[spec])

        async with injector.context(MagicMock()) as service:
            assert isinstance(service, PresetSandboxSpecService)
            assert await service.get_sandbox_spec(DEFAULT_TEMPLATE_NAME) == spec
            assert await service.get_default_sandbox_spec() == spec

    @pytest.mark.asyncio
    async def test_yields_default_specs(self, monkeypatch):
        monkeypatch.delenv('E2B_TEMPLATE', raising=False)

        async with E2BSandboxSpecServiceInjector().context(MagicMock()) as service:
            page = await service.search_sandbox_specs()

        assert [item.id for item in page.items] == [DEFAULT_TEMPLATE_NAME]


class TestInitApiKeySecrecy:
    def test_not_in_sandbox_spec_search_payload(self, spec):
        """The search endpoint responds with a SandboxSpecInfoPage, which drops
        fields the subclass added."""
        payload = SandboxSpecInfoPage(items=[spec]).model_dump_json()

        assert INIT_API_KEY not in payload
        assert 'init_api_key' not in payload

    def test_masked_even_when_dumped_directly(self, spec):
        assert INIT_API_KEY not in spec.model_dump_json()
        assert spec.model_dump()['init_api_key'] == SecretStr(INIT_API_KEY)
        assert str(spec.model_dump()['init_api_key']) == '**********'

    def test_recoverable_by_the_service(self, spec):
        assert spec.init_api_key is not None
        assert spec.init_api_key.get_secret_value() == INIT_API_KEY
