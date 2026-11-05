"""Tests for PodmanSandboxSpecServiceInjector.

This module tests the Podman sandbox spec service injector implementation,
focusing on:
- Initialization with default and custom specs
- Podman image pulling when specs are missing
- Proper mocking of Podman client operations (via docker-py compat API)
- Error handling for API failures
- Socket discovery in ``_connect_to_podman``
"""

from unittest.mock import MagicMock, patch

import pytest
from docker.errors import APIError, DockerException, ImageNotFound
from fastapi import Request
from starlette.datastructures import State

from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.podman_sandbox_spec_service import (
    PodmanSandboxSpecServiceInjector,
    _connect_to_podman,
    get_default_podman_sandbox_specs,
    get_podman_client,
)
from openhands.app_server.sandbox.preset_sandbox_spec_service import (
    PresetSandboxSpecService,
)
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo


@pytest.fixture
def mock_podman_client():
    mock_client = MagicMock()
    mock_client.images = MagicMock()
    return mock_client


@pytest.fixture
def mock_state():
    return State()


@pytest.fixture
def mock_request():
    request = MagicMock(spec=Request)
    request.state = State()
    return request


@pytest.fixture
def sample_spec():
    return SandboxSpecInfo(
        id='test-image:latest',
        command=['/bin/bash'],
        initial_env={'TEST_VAR': 'test_value'},
        working_dir='/test/workspace',
    )


@pytest.fixture
def sample_specs(sample_spec):
    return [
        sample_spec,
        SandboxSpecInfo(
            id='another-image:v1.0',
            command=['/usr/bin/python'],
            initial_env={'PYTHON_ENV': 'test'},
            working_dir='/python/workspace',
        ),
    ]


class TestPodmanSandboxSpecServiceInjector:
    """Test cases for PodmanSandboxSpecServiceInjector."""

    def test_initialization_with_defaults(self):
        injector = PodmanSandboxSpecServiceInjector()
        default_specs = get_default_podman_sandbox_specs()
        assert len(injector.specs) == len(default_specs)
        assert injector.specs[0].id == default_specs[0].id

    def test_initialization_with_custom_specs(self, sample_specs):
        injector = PodmanSandboxSpecServiceInjector(specs=sample_specs)
        assert injector.specs == sample_specs

    @patch('openhands.app_server.sandbox.podman_sandbox_spec_service.get_podman_client')
    async def test_inject_does_not_touch_podman(
        self, mock_get_podman_client, sample_specs, mock_state
    ):
        injector = PodmanSandboxSpecServiceInjector(specs=sample_specs)
        async for service in injector.inject(mock_state):
            assert isinstance(service, PresetSandboxSpecService)
            assert service.specs == sample_specs
        mock_get_podman_client.assert_not_called()

    @patch('openhands.app_server.sandbox.podman_sandbox_spec_service.get_podman_client')
    async def test_inject_with_request(
        self, mock_get_podman_client, sample_specs, mock_request
    ):
        injector = PodmanSandboxSpecServiceInjector(specs=sample_specs)
        async for service in injector.inject(mock_request.state, mock_request):
            assert isinstance(service, PresetSandboxSpecService)
            assert service.specs == sample_specs
        mock_get_podman_client.assert_not_called()

    @patch('openhands.app_server.sandbox.podman_sandbox_spec_service.get_podman_client')
    async def test_prepare_all_exist(self, mock_get_podman_client, sample_specs):
        mock_client = MagicMock()
        mock_get_podman_client.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()

        injector = PodmanSandboxSpecServiceInjector(specs=sample_specs)
        await injector.prepare()

        assert mock_client.images.get.call_count == len(sample_specs)
        mock_client.images.pull.assert_not_called()

    @patch('openhands.app_server.sandbox.podman_sandbox_spec_service.get_podman_client')
    async def test_prepare_some_missing(self, mock_get_podman_client, sample_specs):
        mock_client = MagicMock()
        mock_get_podman_client.return_value = mock_client

        def mock_get_side_effect(image_id):
            if image_id == 'test-image:latest':
                return MagicMock()
            else:
                raise ImageNotFound('Image not found')

        mock_client.images.get.side_effect = mock_get_side_effect
        mock_client.images.pull.return_value = MagicMock()

        injector = PodmanSandboxSpecServiceInjector(specs=sample_specs)
        await injector.prepare()

        assert mock_client.images.get.call_count == len(sample_specs)
        mock_client.images.pull.assert_called_once_with('another-image:v1.0')

    @patch('openhands.app_server.sandbox.podman_sandbox_spec_service.get_podman_client')
    async def test_pull_spec_if_missing_image_exists(
        self, mock_get_podman_client, sample_spec
    ):
        mock_client = MagicMock()
        mock_get_podman_client.return_value = mock_client
        mock_client.images.get.return_value = MagicMock()

        injector = PodmanSandboxSpecServiceInjector()
        await injector.pull_spec_if_missing(sample_spec)

        mock_client.images.get.assert_called_once_with('test-image:latest')
        mock_client.images.pull.assert_not_called()

    @patch('openhands.app_server.sandbox.podman_sandbox_spec_service.get_podman_client')
    async def test_pull_spec_if_missing_image_not_found(
        self, mock_get_podman_client, sample_spec
    ):
        mock_client = MagicMock()
        mock_get_podman_client.return_value = mock_client
        mock_client.images.get.side_effect = ImageNotFound('Image not found')
        mock_client.images.pull.return_value = MagicMock()

        injector = PodmanSandboxSpecServiceInjector()
        await injector.pull_spec_if_missing(sample_spec)

        mock_client.images.get.assert_called_once_with('test-image:latest')
        mock_client.images.pull.assert_called_once_with('test-image:latest')

    @patch('openhands.app_server.sandbox.podman_sandbox_spec_service.get_podman_client')
    async def test_pull_spec_if_missing_api_error(
        self, mock_get_podman_client, sample_spec
    ):
        mock_client = MagicMock()
        mock_get_podman_client.return_value = mock_client
        mock_client.images.get.side_effect = APIError('Podman daemon error')

        injector = PodmanSandboxSpecServiceInjector()

        with pytest.raises(SandboxError, match='Error Getting Podman Image'):
            await injector.pull_spec_if_missing(sample_spec)

    def test_get_default_podman_sandbox_specs(self):
        specs = get_default_podman_sandbox_specs()
        assert len(specs) == 1
        assert isinstance(specs[0], SandboxSpecInfo)
        assert specs[0].id.startswith('ghcr.io/openhands/agent-server:')
        assert specs[0].id.endswith('-python')
        assert specs[0].command == [
            '/usr/local/bin/openhands-agent-server',
            '--port',
            '8000',
        ]
        assert 'OPENVSCODE_SERVER_ROOT' in specs[0].initial_env
        assert specs[0].working_dir == '/workspace/project'

    @patch(
        'openhands.app_server.sandbox.podman_sandbox_spec_service._global_podman_client',
        None,
    )
    @patch(
        'openhands.app_server.sandbox.podman_sandbox_spec_service._connect_to_podman'
    )
    def test_get_podman_client_creates_new_client(self, mock_connect_to_podman):
        mock_client = MagicMock()
        mock_connect_to_podman.return_value = mock_client

        result = get_podman_client()

        assert result == mock_client
        mock_connect_to_podman.assert_called_once()

    @patch(
        'openhands.app_server.sandbox.podman_sandbox_spec_service._connect_to_podman'
    )
    def test_get_podman_client_reuses_existing_client(self, mock_connect_to_podman):
        mock_client = MagicMock()

        with patch(
            'openhands.app_server.sandbox.podman_sandbox_spec_service'
            '._global_podman_client',
            mock_client,
        ):
            result = get_podman_client()

        assert result == mock_client
        mock_connect_to_podman.assert_not_called()

    async def test_inject_yields_single_service(self, sample_specs, mock_state):
        injector = PodmanSandboxSpecServiceInjector(specs=sample_specs)

        services = []
        async for service in injector.inject(mock_state):
            services.append(service)

        assert len(services) == 1
        assert isinstance(services[0], PresetSandboxSpecService)


class TestConnectToPodman:
    """Tests for the Podman socket discovery logic."""

    def test_connect_with_docker_host_env(self):
        """DOCKER_HOST env var is tried first."""
        mock_client = MagicMock()

        with (
            patch.dict('os.environ', {'DOCKER_HOST': 'unix:///tmp/test.sock'}),
            patch('docker.DockerClient', return_value=mock_client),
        ):
            result = _connect_to_podman()
            assert result == mock_client

    def test_connect_with_podman_socket_env(self):
        """PODMAN_SOCKET env var is tried when DOCKER_HOST is unset."""
        mock_client = MagicMock()

        env = {'PODMAN_SOCKET': 'unix:///tmp/podman.sock'}
        with (
            patch.dict('os.environ', env, clear=True),
            patch('docker.DockerClient', return_value=mock_client),
        ):
            result = _connect_to_podman()
            assert result == mock_client

    def test_connect_all_failures_raises_sandbox_error(self):
        """When all connection attempts fail, a SandboxError is raised."""
        with (
            patch.dict('os.environ', {}, clear=True),
            patch(
                'docker.DockerClient',
                side_effect=DockerException('Connection refused'),
            ),
            patch('docker.from_env', side_effect=DockerException('No socket')),
        ):
            with pytest.raises(SandboxError, match='Could not connect to the Podman'):
                _connect_to_podman()
