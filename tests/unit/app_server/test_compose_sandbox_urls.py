"""Compose keeps local routing while enabling private ports behind HTTPS ingress."""

import os

import pytest
from pydantic import TypeAdapter

from containers.compose.start import configure_sandboxes
from openhands.app_server.sandbox.sandbox_provider_config import DockerTemplate


@pytest.mark.parametrize('domain', [None, 'sandboxes.example.com'])
def test_compose_sandbox_routing_and_callbacks(
    monkeypatch: pytest.MonkeyPatch, domain: str | None
) -> None:
    monkeypatch.setenv('COMPOSE_AGENT_SERVER_IMAGE', 'test-image')
    monkeypatch.setenv('COMPOSE_SANDBOX_NETWORK', 'test_sandboxes')
    monkeypatch.setenv('OH_SANDBOX_CALLBACK_URL', 'http://openhands:3000/')
    monkeypatch.delenv('COMPOSE_SANDBOX_DOMAIN', raising=False)
    monkeypatch.delenv('SANDBOX_TEMPLATES', raising=False)
    if domain:
        monkeypatch.setenv('COMPOSE_SANDBOX_DOMAIN', domain)
    configure_sandboxes()

    template = TypeAdapter(list[DockerTemplate]).validate_json(
        os.environ['SANDBOX_TEMPLATES']
    )[0]
    assert template.docker.network == 'test_sandboxes'
    assert template.docker.webhook_url == 'http://openhands:3000/api/v1/webhooks'
    assert template.docker.container_url_pattern == 'http://localhost:{port}'
    assert template.docker.ports == {
        'AGENT_SERVER': 8000,
        'VSCODE': 8001,
        'WORKER_1': 8011,
        'WORKER_2': 8012,
    }
    assert template.docker.bind_host == ('127.0.0.1' if domain else '0.0.0.0')
    assert template.docker.public_url_pattern == (
        'https://{container_port}-{resource_id}.' + domain if domain else None
    )
