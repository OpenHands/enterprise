"""Tests for the Kubernetes agent-sandbox spec service's defaults."""

from openhands.app_server.sandbox.k8s_agent_sandbox_spec_service import (
    DEFAULT_WARM_POOL_NAME,
    DEFAULT_WORKING_DIR,
    get_default_sandbox_specs,
)


class TestDefaults:
    def test_default_spec_keeps_the_state_on_the_workspace_volume(self, monkeypatch):
        """Unset, the state stays where a single-volume template keeps it."""
        monkeypatch.delenv('AGENT_SANDBOX_WARM_POOL', raising=False)
        monkeypatch.delenv('AGENT_SANDBOX_INIT_API_KEY', raising=False)
        monkeypatch.delenv('AGENT_SANDBOX_STATE_DIR', raising=False)

        [spec] = get_default_sandbox_specs()

        assert spec.id == DEFAULT_WARM_POOL_NAME
        assert spec.working_dir == DEFAULT_WORKING_DIR
        assert spec.init_api_key is None
        assert spec.state_dir is None

    def test_default_spec_takes_the_state_dir_from_env(self, monkeypatch):
        monkeypatch.setenv('AGENT_SANDBOX_STATE_DIR', '/var/openhands')

        [spec] = get_default_sandbox_specs()

        assert spec.state_dir == '/var/openhands'

    def test_an_empty_state_dir_counts_as_unset(self, monkeypatch):
        monkeypatch.setenv('AGENT_SANDBOX_STATE_DIR', '')

        [spec] = get_default_sandbox_specs()

        assert spec.state_dir is None
