"""Tests for PodmanSandboxService.

This module tests the Podman sandbox service implementation, focusing on the
differences from DockerSandboxService:

- ``backend`` is ``podman``, not ``docker``
- ``_host_gateway`` returns ``host.containers.internal``
- ``start_sandbox`` builds webhook and LLM-refresh URLs with
  ``host.containers.internal`` instead of ``host.docker.internal``
- ``extra_hosts`` defaults to Podman's convention
- Container lifecycle still works (inherited from DockerSandboxService)
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from openhands.app_server.sandbox.docker_sandbox_service import (
    CREATED_BY_USER_ID_LABEL,
    MANAGED_LABEL,
    SANDBOX_SPEC_ID_LABEL,
    ExposedPort,
    VolumeMount,
)
from openhands.app_server.sandbox.podman_sandbox_service import (
    PodmanSandboxService,
    PodmanSandboxServiceInjector,
)
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    VSCODE,
    WORKER_1,
    WORKER_2,
)
from openhands.app_server.sandbox.sandbox_service import (
    LLM_API_KEY_REFRESH_BASE_URLS_VARIABLE,
    LLM_API_KEY_REFRESH_HEADERS_VALUE,
    LLM_API_KEY_REFRESH_HEADERS_VARIABLE,
    LLM_API_KEY_REFRESH_URL_VARIABLE,
    WEBHOOK_CALLBACK_VARIABLE,
)
from openhands.app_server.sandbox.sandbox_store import (
    PODMAN_BACKEND,
    StoredSandbox,
)
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.utils.podman_utils import PODMAN_HOST_GATEWAY

OWNER_ID = 'user123'
CREATED_AT = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)


def _labels(
    sandbox_spec_id: str = 'spec456', created_by_user_id: str = OWNER_ID
) -> dict[str, str]:
    return {
        MANAGED_LABEL: 'true',
        SANDBOX_SPEC_ID_LABEL: sandbox_spec_id,
        CREATED_BY_USER_ID_LABEL: created_by_user_id,
    }


def _user_context(
    user_id: str | None = OWNER_ID, default_sandbox_spec_id: str | None = None
) -> AsyncMock:
    context = AsyncMock(spec=UserContext)
    context.get_user_id.return_value = user_id
    context.get_default_sandbox_spec_id.return_value = default_sandbox_spec_id
    return context


@pytest.fixture
def mock_user_context():
    return _user_context()


@pytest.fixture
async def db_session(async_session_maker):
    async with async_session_maker() as session:
        yield session


@pytest.fixture
def mock_podman_client():
    mock_client = MagicMock()
    mock_client.containers.list.return_value = []
    return mock_client


@pytest.fixture
def mock_sandbox_spec_service():
    mock_service = AsyncMock()
    mock_spec = MagicMock()
    mock_spec.id = 'test-image:latest'
    mock_spec.initial_env = {'TEST_VAR': 'test_value'}
    mock_spec.working_dir = '/workspace'
    mock_service.get_default_sandbox_spec.return_value = mock_spec
    mock_service.get_sandbox_spec.return_value = mock_spec
    return mock_service


@pytest.fixture
def mock_httpx_client():
    client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    client.get.return_value = mock_response
    return client


@pytest.fixture
def service(
    mock_sandbox_spec_service,
    mock_httpx_client,
    mock_podman_client,
    mock_user_context,
    db_session,
):
    return PodmanSandboxService(
        sandbox_spec_service=mock_sandbox_spec_service,
        user_context=mock_user_context,
        db_session=db_session,
        container_name_prefix='oh-test-',
        host_port=3000,
        container_url_pattern='http://localhost:{port}',
        mounts=[
            VolumeMount(host_path='/tmp/test', container_path='/workspace', mode='rw')
        ],
        exposed_ports=[
            ExposedPort(
                name=AGENT_SERVER, description='Agent server', container_port=8000
            ),
            ExposedPort(name=VSCODE, description='VSCode server', container_port=8001),
        ],
        health_check_path='/health',
        httpx_client=mock_httpx_client,
        max_num_sandboxes=3,
        docker_client=mock_podman_client,
    )


class TestPodmanSandboxServiceBasics:
    """Test the Podman-specific overrides."""

    def test_backend_is_podman(self, service):
        assert service.backend == PODMAN_BACKEND

    def test_host_gateway_is_podman_convention(self, service):
        assert service._host_gateway == PODMAN_HOST_GATEWAY
        assert service._host_gateway == 'host.containers.internal'

    def test_host_gateway_differs_from_docker(self, service):
        """The Podman host gateway must not be host.docker.internal."""
        assert service._host_gateway != 'host.docker.internal'

    def test_is_subclass_of_docker_service(self, service):
        """PodmanSandboxService inherits from DockerSandboxService so it
        reuses all container lifecycle, port-finding, and health-check logic."""
        from openhands.app_server.sandbox.docker_sandbox_service import (
            DockerSandboxService,
        )

        assert isinstance(service, DockerSandboxService)


class TestPodmanStartSandbox:
    """Test that start_sandbox uses Podman host-gateway in env vars."""

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_uses_podman_host_gateway(
        self,
        mock_urandom,
        mock_encodebytes,
        service,
        mock_podman_client,
    ):
        """Webhook and LLM refresh URLs use host.containers.internal."""
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']

        mock_container = MagicMock()
        mock_container.name = 'oh-test-test_container_id'
        mock_container.status = 'running'
        mock_container.image.tags = ['test-image:latest']
        mock_container.labels = _labels('test-image:latest')
        mock_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {'Env': ['OH_SESSION_API_KEYS_0=test_session_key']},
            'NetworkSettings': {'Ports': {}},
        }
        mock_podman_client.containers.run.return_value = mock_container

        with (
            patch.object(service, '_find_unused_port', return_value=12345),
            patch.object(service, 'pause_old_sandboxes', return_value=[]),
            patch('server.constants.LITE_LLM_API_URL', 'https://llm-proxy.example'),
        ):
            await service.start_sandbox()

        env_vars = mock_podman_client.containers.run.call_args[1]['environment']

        # Webhook URL uses host.containers.internal
        assert env_vars[WEBHOOK_CALLBACK_VARIABLE] == (
            'http://host.containers.internal:3000/api/v1/webhooks'
        )

        # LLM refresh URL uses host.containers.internal
        assert env_vars[LLM_API_KEY_REFRESH_URL_VARIABLE] == (
            'http://host.containers.internal:3000/api/keys/llm/managed/current'
        )

        # Refresh headers contract is unchanged
        assert (
            env_vars[LLM_API_KEY_REFRESH_HEADERS_VARIABLE]
            == LLM_API_KEY_REFRESH_HEADERS_VALUE
        )

        # Base URLs carry the managed proxy URL
        assert (
            env_vars[LLM_API_KEY_REFRESH_BASE_URLS_VARIABLE]
            == 'https://llm-proxy.example'
        )

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_does_not_use_docker_host_gateway(
        self,
        mock_urandom,
        mock_encodebytes,
        service,
        mock_podman_client,
    ):
        """Ensure no env var contains host.docker.internal."""
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']

        mock_container = MagicMock()
        mock_container.name = 'oh-test-test_container_id'
        mock_container.status = 'running'
        mock_container.image.tags = ['test-image:latest']
        mock_container.labels = _labels('test-image:latest')
        mock_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {'Env': ['OH_SESSION_API_KEYS_0=test_session_key']},
            'NetworkSettings': {'Ports': {}},
        }
        mock_podman_client.containers.run.return_value = mock_container

        with (
            patch.object(service, '_find_unused_port', return_value=12345),
            patch.object(service, 'pause_old_sandboxes', return_value=[]),
            patch('server.constants.LITE_LLM_API_URL', ''),
        ):
            await service.start_sandbox()

        env_vars = mock_podman_client.containers.run.call_args[1]['environment']
        for key, value in env_vars.items():
            if isinstance(value, str):
                assert 'host.docker.internal' not in value, (
                    f'env var {key} should not contain host.docker.internal'
                )


class TestPodmanContainerLifecycle:
    """Test that lifecycle methods work via the inherited Docker logic."""

    @pytest.fixture
    def mock_running_container(self):
        container = MagicMock()
        container.name = 'oh-test-abc123'
        container.status = 'running'
        container.labels = _labels('spec456')
        container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {
                'Env': [
                    'OH_SESSION_API_KEYS_0=session_key_123',
                    'OTHER_VAR=other_value',
                ],
                'WorkingDir': '/workspace',
            },
            'NetworkSettings': {
                'Ports': {
                    '8000/tcp': [{'HostPort': '12345'}],
                    '8001/tcp': [{'HostPort': '12346'}],
                }
            },
        }
        return container

    @pytest.fixture
    def mock_paused_container(self):
        container = MagicMock()
        container.name = 'oh-test-def456'
        container.status = 'paused'
        container.labels = _labels('spec456')
        container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {'Env': []},
            'NetworkSettings': {'Ports': {}},
        }
        return container

    async def test_pause_sandbox(self, service, mock_running_container, db_session):
        """Pausing a running sandbox stops the container."""
        sandbox = StoredSandbox(
            id='oh-test-abc123',
            backend=PODMAN_BACKEND,
            created_by_user_id=OWNER_ID,
            sandbox_spec_id='spec456',
            created_at=CREATED_AT,
        )
        db_session.add(sandbox)
        await db_session.flush()

        service.docker_client.containers.get.return_value = mock_running_container

        result = await service.pause_sandbox('oh-test-abc123')
        assert result is True
        mock_running_container.stop.assert_called_once()

    async def test_resume_sandbox(self, service, mock_paused_container, db_session):
        """Resuming a paused sandbox unpauses the container."""
        # Need a fresh service with the right container mock
        sandbox = StoredSandbox(
            id='oh-test-def456',
            backend=PODMAN_BACKEND,
            created_by_user_id=OWNER_ID,
            sandbox_spec_id='spec456',
            created_at=CREATED_AT,
        )
        db_session.add(sandbox)
        await db_session.flush()

        service.docker_client.containers.get.return_value = mock_paused_container

        result = await service.resume_sandbox('oh-test-def456')
        assert result is True
        mock_paused_container.unpause.assert_called_once()

    async def test_delete_sandbox(self, service, mock_running_container, db_session):
        """Deleting a running sandbox stops and removes the container."""
        sandbox = StoredSandbox(
            id='oh-test-abc123',
            backend=PODMAN_BACKEND,
            created_by_user_id=OWNER_ID,
            sandbox_spec_id='spec456',
            created_at=CREATED_AT,
        )
        db_session.add(sandbox)
        await db_session.flush()

        service.docker_client.containers.get.return_value = mock_running_container

        result = await service.delete_sandbox('oh-test-abc123')
        assert result is True
        mock_running_container.stop.assert_called_once()
        mock_running_container.remove.assert_called_once()

    async def test_delete_sandbox_already_removed(self, service, db_session):
        """Deleting a sandbox whose container is gone still succeeds."""
        sandbox = StoredSandbox(
            id='oh-test-gone',
            backend=PODMAN_BACKEND,
            created_by_user_id=OWNER_ID,
            sandbox_spec_id='spec456',
            created_at=CREATED_AT,
        )
        db_session.add(sandbox)
        await db_session.flush()

        service.docker_client.containers.get.return_value = None

        result = await service.delete_sandbox('oh-test-gone')
        assert result is True


class TestPodmanSandboxServiceInjector:
    """Test the injector configuration."""

    def test_backend_is_podman(self):
        injector = PodmanSandboxServiceInjector()
        assert injector.backend == PODMAN_BACKEND

    def test_default_extra_hosts_uses_podman_convention(self):
        injector = PodmanSandboxServiceInjector()
        assert PODMAN_HOST_GATEWAY in injector.extra_hosts
        assert injector.extra_hosts[PODMAN_HOST_GATEWAY] == 'host-gateway'

    def test_default_extra_hosts_does_not_use_docker_convention(self):
        injector = PodmanSandboxServiceInjector()
        assert 'host.docker.internal' not in injector.extra_hosts

    def test_default_exposed_ports(self):
        injector = PodmanSandboxServiceInjector()
        assert len(injector.exposed_ports) == 4
        names = [ep.name for ep in injector.exposed_ports]
        assert AGENT_SERVER in names
        assert VSCODE in names
        assert WORKER_1 in names
        assert WORKER_2 in names

    def test_custom_extra_hosts(self):
        injector = PodmanSandboxServiceInjector(extra_hosts={'custom.host': '1.2.3.4'})
        assert injector.extra_hosts == {'custom.host': '1.2.3.4'}
