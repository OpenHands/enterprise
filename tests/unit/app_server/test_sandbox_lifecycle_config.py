"""The lifecycle settings, read from env by ``config_from_env``."""

import pytest

from openhands.app_server.config import config_from_env
from openhands.app_server.sandbox.lifecycle.settings import SandboxLifecycleSettings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ('RUNTIME', 'OH_SANDBOX_KIND'):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize('runtime', ['docker', 'e2b', 'k8s-agent-sandbox'])
def test_the_backend_reads_the_settings_when_runtime_picks_it(monkeypatch, runtime):
    monkeypatch.setenv('RUNTIME', runtime)
    monkeypatch.setenv('OH_SANDBOX_LIFECYCLE_IDLE_SECONDS', '60')

    config = config_from_env()

    assert config.sandbox.lifecycle == SandboxLifecycleSettings(idle_seconds=60)


def test_the_backend_reads_the_settings_when_its_kind_is_set(monkeypatch):
    monkeypatch.setenv('OH_SANDBOX_KIND', 'E2BSandboxServiceInjector')
    monkeypatch.setenv('OH_SANDBOX_LIFECYCLE_MAX_SESSION_SECONDS', '0')

    config = config_from_env()

    assert config.sandbox.lifecycle == SandboxLifecycleSettings(max_session_seconds=0)


def test_the_defaults_apply_without_env(monkeypatch):
    monkeypatch.setenv('RUNTIME', 'e2b')

    assert config_from_env().sandbox.lifecycle == SandboxLifecycleSettings()
