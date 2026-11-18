"""Network metadata must select reachable sandbox URLs without engine I/O."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from openhands.app_server.sandbox.docker_sandbox_service import (
    DockerSandboxService,
    ExposedPort,
)
from openhands.app_server.sandbox.sandbox_models import AGENT_SERVER, VSCODE
from openhands.app_server.sandbox.sandbox_store import DOCKER_BACKEND, StoredSandbox


@pytest.mark.parametrize(
    'network,network_mode,network_settings,expected_ports',
    [
        pytest.param(
            'configured', 'configured', {}, (8000, 8001), id='docker-named-network'
        ),
        pytest.param(
            'configured',
            'bridge',
            {'Networks': {'configured': {}}, 'Ports': {}},
            (8000, 8001),
            id='podman-named-network',
        ),
        pytest.param(
            'configured',
            'network-id',
            {'Networks': {'configured': {}}},
            (8000, 8001),
            id='network-mode-is-id',
        ),
        pytest.param(
            None,
            'bridge',
            {
                'Networks': {'bridge': {}},
                'Ports': {
                    '8000/tcp': [{'HostPort': '43110'}],
                    '8001/tcp': [{'HostPort': '43111'}],
                },
            },
            (43110, 43111),
            id='default-bridge-published-ports',
        ),
        pytest.param('configured', 'host', {}, (8000, 8001), id='host-network'),
        pytest.param(
            'configured',
            'bridge',
            {'Networks': {'other': {}}},
            (),
            id='unrelated-network-no-ports',
        ),
        pytest.param(
            'configured',
            'bridge',
            {
                'Networks': {'other': {}},
                'Ports': {
                    '8000/tcp': [{'HostPort': '43110'}],
                    '8001/tcp': [{'HostPort': '43111'}],
                },
            },
            (43110, 43111),
            id='unrelated-network-published-ports',
        ),
        pytest.param('configured', 'bridge', {}, (), id='missing-networks'),
        pytest.param(
            'configured', 'bridge', {'Networks': None}, (), id='null-networks'
        ),
        pytest.param(
            None,
            'bridge',
            {'Networks': {'configured': {}}},
            (),
            id='no-configured-network',
        ),
    ],
)
async def test_network_metadata_selects_sandbox_urls(
    network, network_mode, network_settings, expected_ports
):
    """Run the real conversion method; only provider metadata is simulated."""
    service = DockerSandboxService(
        user_context=AsyncMock(),
        db_session=AsyncMock(),
        httpx_client=AsyncMock(),
        max_num_sandboxes=1,
        sandbox_spec_service=AsyncMock(),
        container_name_prefix='oh-test-',
        host_port=3000,
        container_url_pattern='http://proxy.invalid/runtime/{sandbox_id}/{port}',
        mounts=[],
        exposed_ports=[
            ExposedPort(name=AGENT_SERVER, description='Agent', container_port=8000),
            ExposedPort(name=VSCODE, description='VSCode', container_port=8001),
        ],
        health_check_path=None,
        docker_client=MagicMock(),
        network=network,
    )
    stored = StoredSandbox(
        id='sandbox',
        backend=DOCKER_BACKEND,
        created_by_user_id='owner',
        sandbox_spec_id='spec',
        created_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
    )
    container = MagicMock()
    container.name = 'sandbox'
    container.status = 'running'
    container.attrs = {
        'HostConfig': {'NetworkMode': network_mode},
        'NetworkSettings': network_settings,
        'Config': {
            'Env': ['OH_SESSION_API_KEYS_0=synthetic-test-key'],
            'WorkingDir': '/workspace',
        },
    }

    result = await service._to_sandbox_info(stored, container)

    urls = {url.name: url.url for url in result.exposed_urls or []}
    if expected_ports:
        agent_port, vscode_port = expected_ports
        assert urls == {
            AGENT_SERVER: f'http://proxy.invalid/runtime/sandbox/{agent_port}',
            VSCODE: (
                f'http://proxy.invalid/runtime/sandbox/{vscode_port}'
                '/?tkn=synthetic-test-key&folder=/workspace'
            ),
        }
    else:
        assert urls == {}
    service.docker_client.containers.run.assert_not_called()
    service.httpx_client.get.assert_not_called()
