"""Tests for DockerSandboxService.

This module tests the Docker sandbox service implementation, focusing on:
- `v1_sandbox` as the store for ownership and spec identity
- user scoping, including cross user isolation and the admin (no user id) case
- container lifecycle management (start, pause, resume, delete)
- search and retrieval with pagination off the table
- health checking and URL generation
- error handling for Docker API failures
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from docker.errors import APIError, DockerException, NotFound

from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox import docker_sandbox_spec_service
from openhands.app_server.sandbox.docker_sandbox_service import (
    CREATED_BY_USER_ID_LABEL,
    MANAGED_LABEL,
    SANDBOX_SPEC_ID_LABEL,
    DockerSandboxService,
    ExposedPort,
    VolumeMount,
)
from openhands.app_server.sandbox.docker_sandbox_spec_service import (
    _connect_to_docker,
    get_docker_client,
)
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    VSCODE,
    SandboxPage,
    SandboxStatus,
)
from openhands.app_server.sandbox.sandbox_store import (
    DOCKER_BACKEND,
    StoredSandbox,
    hash_session_api_key,
)

OWNER_ID = 'user123'
CREATED_AT = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)


def _labels(
    sandbox_spec_id: str = 'spec456', created_by_user_id: str | None = None
) -> dict[str, str]:
    """Container labels as start_sandbox writes them."""
    labels = {MANAGED_LABEL: 'true', SANDBOX_SPEC_ID_LABEL: sandbox_spec_id}
    if created_by_user_id:
        labels[CREATED_BY_USER_ID_LABEL] = created_by_user_id
    return labels


def _stored(
    sandbox_id: str,
    created_by_user_id: str | None = None,
    sandbox_spec_id: str = 'spec456',
    session_api_key: str | None = None,
    created_at: datetime | None = None,
) -> StoredSandbox:
    """The `v1_sandbox` row start_sandbox writes for a container."""
    return StoredSandbox(
        id=sandbox_id,
        backend=DOCKER_BACKEND,
        created_by_user_id=created_by_user_id,
        sandbox_spec_id=sandbox_spec_id,
        session_api_key_hash=(
            hash_session_api_key(session_api_key) if session_api_key else None
        ),
        created_at=created_at or CREATED_AT,
    )


def _user_context(
    user_id: str | None = None, default_sandbox_spec_id: str | None = None
) -> AsyncMock:
    """Mock UserContext resolving to the given user and default spec."""
    context = AsyncMock()
    context.get_user_id.return_value = user_id
    context.get_default_sandbox_spec_id.return_value = default_sandbox_spec_id
    return context


@pytest.fixture
def mock_user_context():
    """UserContext for a caller with no user id (OSS single user / admin)."""
    return _user_context()


@pytest.fixture
async def db_session(async_session_maker):
    """A session on this test's own postgres database."""
    async with async_session_maker() as session:
        yield session


@pytest.fixture
def store(db_session):
    """Add the rows standing for the containers a test sets up."""

    async def _store(*sandboxes: StoredSandbox) -> None:
        for sandbox in sandboxes:
            db_session.add(sandbox)
        await db_session.flush()

    return _store


@pytest.fixture
def mock_docker_client():
    """Mock Docker client for testing."""
    mock_client = MagicMock()
    mock_client.containers.list.return_value = []
    return mock_client


@pytest.fixture
def mock_sandbox_spec_service():
    """Mock SandboxSpecService for testing."""
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
    """Mock httpx AsyncClient for testing."""
    client = AsyncMock(spec=httpx.AsyncClient)
    # Configure the mock response
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    client.get.return_value = mock_response
    return client


@pytest.fixture
def service(
    mock_sandbox_spec_service,
    mock_httpx_client,
    mock_docker_client,
    mock_user_context,
    db_session,
):
    """Create DockerSandboxService instance for testing."""
    return DockerSandboxService(
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
        docker_client=mock_docker_client,
    )


@pytest.fixture
def mock_running_container():
    """Create a mock running Docker container."""
    container = MagicMock()
    container.name = 'oh-test-abc123'
    container.status = 'running'
    container.labels = _labels('spec456')
    container.attrs = {
        'Created': '2024-01-15T10:30:00.000000000Z',
        'Config': {
            'Env': ['OH_SESSION_API_KEYS_0=session_key_123', 'OTHER_VAR=other_value'],
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
def mock_paused_container():
    """Create a mock paused Docker container."""
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


@pytest.fixture
def mock_exited_container():
    """Create a mock exited Docker container."""
    container = MagicMock()
    container.name = 'oh-test-ghi789'
    container.status = 'exited'
    container.labels = _labels('spec456', OWNER_ID)
    container.attrs = {
        'Created': '2024-01-15T10:30:00.000000000Z',
        'Config': {'Env': []},
        'NetworkSettings': {'Ports': {}},
    }
    return container


class TestDockerSandboxService:
    """Test cases for DockerSandboxService."""

    async def test_search_sandboxes_success(
        self, service, store, mock_running_container, mock_paused_container
    ):
        """Test successful search for sandboxes."""
        # Setup
        await store(
            _stored('oh-test-abc123', session_api_key='session_key_123'),
            _stored('oh-test-def456'),
        )
        service.docker_client.containers.list.return_value = [
            mock_running_container,
            mock_paused_container,
        ]
        service.httpx_client.get.return_value.raise_for_status.return_value = None

        # Execute
        result = await service.search_sandboxes()

        # Verify
        assert isinstance(result, SandboxPage)
        assert len(result.items) == 2
        assert result.next_page_id is None

        # Verify running container
        running_sandbox = next(
            s for s in result.items if s.status == SandboxStatus.RUNNING
        )
        assert running_sandbox.id == 'oh-test-abc123'
        assert running_sandbox.created_by_user_id is None
        assert running_sandbox.sandbox_spec_id == 'spec456'
        assert running_sandbox.session_api_key == 'session_key_123'
        assert len(running_sandbox.exposed_urls) == 2

        # Verify paused container
        paused_sandbox = next(
            s for s in result.items if s.status == SandboxStatus.PAUSED
        )
        assert paused_sandbox.id == 'oh-test-def456'
        assert paused_sandbox.session_api_key is None
        assert paused_sandbox.exposed_urls is None

    async def test_search_sandboxes_pagination(self, service, store):
        """Test pagination functionality."""
        # Setup - create multiple containers
        await store(
            *[
                _stored(
                    f'oh-test-container{i}',
                    session_api_key=f'session_key_{i}',
                    created_at=CREATED_AT + timedelta(days=i),
                )
                for i in range(5)
            ]
        )
        containers = []
        for i in range(5):
            container = MagicMock()
            container.name = f'oh-test-container{i}'
            container.status = 'running'
            container.labels = _labels('spec456')
            container.attrs = {
                'Created': f'2024-01-{15 + i:02d}T10:30:00.000000000Z',
                'Config': {
                    'Env': [
                        f'OH_SESSION_API_KEYS_0=session_key_{i}',
                        f'OTHER_VAR=value_{i}',
                    ]
                },
                'NetworkSettings': {'Ports': {}},
            }
            containers.append(container)

        service.docker_client.containers.list.return_value = containers
        service.httpx_client.get.return_value.raise_for_status.return_value = None

        # Execute - first page
        result = await service.search_sandboxes(limit=3)

        # Verify first page
        assert len(result.items) == 3
        assert result.next_page_id == '3'

        # Execute - second page
        result = await service.search_sandboxes(page_id='3', limit=3)

        # Verify second page
        assert len(result.items) == 2
        assert result.next_page_id is None

    async def test_search_sandboxes_invalid_page_id(
        self, service, store, mock_running_container
    ):
        """Test handling of invalid page ID."""
        # Setup
        await store(_stored('oh-test-abc123'))
        service.docker_client.containers.list.return_value = [mock_running_container]
        service.httpx_client.get.return_value.raise_for_status.return_value = None

        # Execute
        result = await service.search_sandboxes(page_id='invalid')

        # Verify - should start from beginning
        assert len(result.items) == 1

    async def test_search_sandboxes_docker_api_error(self, service, store):
        """Test that a daemon failure is reported, not read as an empty host.

        An empty page hides the sandbox limit from `pause_old_sandboxes`, and
        a page of MISSING archives every live conversation for the length of
        the outage.
        """
        # Setup
        await store(_stored('oh-test-abc123'))
        service.docker_client.containers.list.side_effect = APIError(
            'Docker daemon error'
        )

        # Execute / Verify
        with pytest.raises(SandboxError, match='Could not list containers'):
            await service.search_sandboxes()

    async def test_get_sandbox_reads_spec_id_from_the_row(self, service, store):
        """Test that the spec id comes from the row, not the container.

        When an image is rebuilt under the same tag the old container's image
        loses its tags, so nothing on the container names the spec the sandbox
        was started from. The row does.
        """
        await store(_stored('oh-test-tagless', sandbox_spec_id='locally-built:dev'))
        tagless_container = MagicMock()
        tagless_container.name = 'oh-test-tagless'
        tagless_container.status = 'paused'
        tagless_container.image.tags = []
        tagless_container.image.id = 'sha256:abc123def456'
        tagless_container.labels = _labels('locally-built:dev')
        tagless_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {'Env': []},
            'NetworkSettings': {'Ports': {}},
        }
        service.docker_client.containers.get.return_value = tagless_container

        # Execute
        result = await service.get_sandbox('oh-test-tagless')

        # Verify
        assert result is not None
        assert result.sandbox_spec_id == 'locally-built:dev'

    async def test_search_sandboxes_makes_one_daemon_call(self, service, store):
        """Test that search asks the daemon once, for managed containers."""
        # Setup
        await store(_stored('oh-test-abc123'), _stored('oh-test-def456'))
        service.docker_client.containers.list.return_value = []

        # Execute
        await service.search_sandboxes()

        # Verify
        service.docker_client.containers.list.assert_called_once_with(
            all=True, filters={'label': f'{MANAGED_LABEL}=true'}
        )

    async def test_search_sandboxes_reports_a_rowless_container_nowhere(
        self, service, mock_running_container
    ):
        """Test that a container with no row is invisible."""
        # Setup
        service.docker_client.containers.list.return_value = [mock_running_container]

        # Execute
        result = await service.search_sandboxes()

        # Verify
        assert result.items == []

    async def test_get_sandbox_skips_container_without_a_row(self, service):
        """Test that a container the app has no record of is invisible."""
        # Setup
        unmanaged_container = MagicMock()
        unmanaged_container.name = 'oh-test-unmanaged'
        unmanaged_container.status = 'running'
        unmanaged_container.labels = {}
        service.docker_client.containers.get.return_value = unmanaged_container

        # Execute
        result = await service.get_sandbox('oh-test-unmanaged')

        # Verify
        assert result is None

    async def test_get_sandbox_without_a_container_is_missing(self, service, store):
        """Test that a row whose container is gone reports MISSING."""
        # Setup
        await store(_stored('oh-test-reaped', created_by_user_id=OWNER_ID))
        service.docker_client.containers.get.side_effect = NotFound('gone')

        # Execute
        result = await service.get_sandbox('oh-test-reaped')

        # Verify
        assert result is not None
        assert result.status == SandboxStatus.MISSING
        assert result.created_by_user_id == OWNER_ID
        assert result.session_api_key is None
        assert result.exposed_urls is None

    async def test_get_sandbox_success(self, service, store, mock_running_container):
        """Test successful retrieval of specific sandbox."""
        # Setup
        await store(_stored('oh-test-abc123', session_api_key='session_key_123'))
        service.docker_client.containers.get.return_value = mock_running_container
        service.httpx_client.get.return_value.raise_for_status.return_value = None

        # Execute
        result = await service.get_sandbox('oh-test-abc123')

        # Verify
        assert result is not None
        assert result.id == 'oh-test-abc123'
        assert result.status == SandboxStatus.RUNNING

        # Verify Docker client was called correctly
        service.docker_client.containers.get.assert_called_once_with('oh-test-abc123')

    async def test_get_sandbox_not_found(self, service):
        """Test handling when sandbox is not found."""
        # Setup - no row for this id
        service.docker_client.containers.get.side_effect = NotFound(
            'Container not found'
        )

        # Execute
        result = await service.get_sandbox('oh-test-nonexistent')

        # Verify
        assert result is None

    async def test_get_sandbox_wrong_owner(
        self, service, store, mock_running_container
    ):
        """Test handling when the sandbox belongs to another user."""
        # Setup
        await store(_stored('oh-test-abc123', created_by_user_id='user-b'))
        service.user_context.get_user_id.return_value = 'user-a'
        service.docker_client.containers.get.return_value = mock_running_container

        # Execute
        result = await service.get_sandbox('oh-test-abc123')

        # Verify
        assert result is None

    async def test_get_sandbox_api_error(self, service, store):
        """Test that a daemon failure is reported rather than read as MISSING."""
        # Setup
        await store(_stored('oh-test-abc123'))
        service.docker_client.containers.get.side_effect = APIError(
            'Docker daemon error'
        )

        # Execute / Verify
        with pytest.raises(SandboxError, match='Could not read container'):
            await service.get_sandbox('oh-test-abc123')

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_success(self, mock_urandom, mock_encodebytes, service):
        """Test successful sandbox startup."""
        # Setup
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']

        mock_container = MagicMock()
        mock_container.name = 'oh-test-test_container_id'
        mock_container.status = 'running'
        mock_container.labels = _labels('test-image:latest')
        mock_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {
                'Env': ['OH_SESSION_API_KEYS_0=test_session_key', 'TEST_VAR=test_value']
            },
            'NetworkSettings': {'Ports': {}},
        }

        service.docker_client.containers.run.return_value = mock_container

        with (
            patch.object(service, '_find_unused_port', side_effect=[12345, 12346]),
            patch.object(
                service, 'pause_old_sandboxes', return_value=[]
            ) as mock_cleanup,
        ):
            # Execute
            result = await service.start_sandbox()

        # Verify
        assert result is not None
        assert result.id == 'oh-test-test_container_id'

        # Verify cleanup was called with the correct limit
        mock_cleanup.assert_called_once_with(2)

        # Verify container was created with correct parameters
        service.docker_client.containers.run.assert_called_once()
        call_args = service.docker_client.containers.run.call_args

        assert call_args[1]['image'] == 'test-image:latest'
        assert call_args[1]['name'] == 'oh-test-test_container_id'
        assert 'OH_SESSION_API_KEYS_0' in call_args[1]['environment']
        assert (
            call_args[1]['environment']['OH_SESSION_API_KEYS_0'] == 'test_session_key'
        )
        assert call_args[1]['ports'] == {8000: 12345, 8001: 12346}
        assert call_args[1]['working_dir'] == '/workspace'
        assert call_args[1]['detach'] is True

    async def test_start_sandbox_with_spec_id(self, service, mock_sandbox_spec_service):
        """Test starting sandbox with specific spec ID."""
        # Setup
        mock_container = MagicMock()
        mock_container.name = 'oh-test-abc123'
        mock_container.status = 'running'
        mock_container.labels = _labels('spec456')
        mock_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {
                'Env': [
                    'OH_SESSION_API_KEYS_0=test_session_key',
                    'OTHER_VAR=test_value',
                ]
            },
            'NetworkSettings': {'Ports': {}},
        }
        service.docker_client.containers.run.return_value = mock_container

        with (
            patch.object(service, '_find_unused_port', return_value=12345),
            patch.object(service, 'pause_old_sandboxes', return_value=[]),
        ):
            # Execute
            await service.start_sandbox(sandbox_spec_id='custom-spec')

        # Verify
        mock_sandbox_spec_service.get_sandbox_spec.assert_called_once_with(
            'custom-spec'
        )

    async def test_start_sandbox_spec_not_found(
        self, service, mock_sandbox_spec_service
    ):
        """Test starting sandbox with non-existent spec ID."""
        # Setup
        mock_sandbox_spec_service.get_sandbox_spec.return_value = None

        # Execute & Verify
        with (
            patch.object(service, 'pause_old_sandboxes', return_value=[]),
            pytest.raises(ValueError, match=r"Sandbox Spec '.*' not found"),
        ):
            await service.start_sandbox(sandbox_spec_id='nonexistent')

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_with_sandbox_id(
        self, mock_urandom, mock_encodebytes, service
    ):
        """Test starting sandbox with a specified sandbox_id."""
        # Setup - only need urandom for session key
        mock_urandom.return_value = b'session_key'
        mock_encodebytes.return_value = 'test_session_key'

        mock_container = MagicMock()
        mock_container.name = 'oh-test-custom_sandbox_id'
        mock_container.status = 'running'
        mock_container.labels = _labels('test-image:latest')
        mock_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {
                'Env': ['OH_SESSION_API_KEYS_0=test_session_key', 'TEST_VAR=test_value']
            },
            'NetworkSettings': {'Ports': {}},
        }

        service.docker_client.containers.run.return_value = mock_container

        with (
            patch.object(service, '_find_unused_port', side_effect=[12345, 12346]),
            patch.object(service, 'pause_old_sandboxes', return_value=[]),
        ):
            # Execute with custom sandbox_id
            result = await service.start_sandbox(sandbox_id='custom_sandbox_id')

        # Verify
        assert result is not None
        assert result.id == 'oh-test-custom_sandbox_id'

        # Verify container was created with the custom sandbox ID in the name
        call_args = service.docker_client.containers.run.call_args
        assert call_args[1]['name'] == 'oh-test-custom_sandbox_id'

    async def test_start_sandbox_docker_error(self, service):
        """Test handling of Docker errors during sandbox startup."""
        # Setup
        service.docker_client.containers.run.side_effect = APIError(
            'Failed to create container'
        )

        with (
            patch.object(service, '_find_unused_port', return_value=12345),
            patch.object(service, 'pause_old_sandboxes', return_value=[]),
            pytest.raises(SandboxError, match='Failed to start container'),
        ):
            await service.start_sandbox()

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_with_extra_hosts(
        self,
        mock_urandom,
        mock_encodebytes,
        mock_sandbox_spec_service,
        mock_user_context,
        mock_httpx_client,
        mock_docker_client,
        db_session,
    ):
        """Test that extra_hosts are passed to container creation."""
        # Setup
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']

        mock_container = MagicMock()
        mock_container.name = 'oh-test-test_container_id'
        mock_container.status = 'running'
        mock_container.labels = _labels('test-image:latest')
        mock_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {
                'Env': ['OH_SESSION_API_KEYS_0=test_session_key', 'TEST_VAR=test_value']
            },
            'NetworkSettings': {'Ports': {}},
        }
        mock_docker_client.containers.run.return_value = mock_container

        # Create service with extra_hosts
        service_with_extra_hosts = DockerSandboxService(
            sandbox_spec_service=mock_sandbox_spec_service,
            user_context=mock_user_context,
            db_session=db_session,
            container_name_prefix='oh-test-',
            host_port=3000,
            container_url_pattern='http://localhost:{port}',
            mounts=[],
            exposed_ports=[
                ExposedPort(
                    name=AGENT_SERVER, description='Agent server', container_port=8000
                ),
            ],
            health_check_path='/health',
            httpx_client=mock_httpx_client,
            max_num_sandboxes=3,
            extra_hosts={
                'host.docker.internal': 'host-gateway',
                'custom.host': '192.168.1.100',
            },
            docker_client=mock_docker_client,
        )

        with (
            patch.object(
                service_with_extra_hosts, '_find_unused_port', return_value=12345
            ),
            patch.object(
                service_with_extra_hosts, 'pause_old_sandboxes', return_value=[]
            ),
        ):
            # Execute
            await service_with_extra_hosts.start_sandbox()

        # Verify extra_hosts was passed to container creation
        mock_docker_client.containers.run.assert_called_once()
        call_args = mock_docker_client.containers.run.call_args
        assert call_args[1]['extra_hosts'] == {
            'host.docker.internal': 'host-gateway',
            'custom.host': '192.168.1.100',
        }

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_without_extra_hosts(
        self,
        mock_urandom,
        mock_encodebytes,
        mock_sandbox_spec_service,
        mock_user_context,
        mock_httpx_client,
        mock_docker_client,
        db_session,
    ):
        """Test that extra_hosts is None when not configured."""
        # Setup
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']

        mock_container = MagicMock()
        mock_container.name = 'oh-test-test_container_id'
        mock_container.status = 'running'
        mock_container.labels = _labels('test-image:latest')
        mock_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {
                'Env': ['OH_SESSION_API_KEYS_0=test_session_key', 'TEST_VAR=test_value']
            },
            'NetworkSettings': {'Ports': {}},
        }
        mock_docker_client.containers.run.return_value = mock_container

        # Create service without extra_hosts (empty dict)
        service_without_extra_hosts = DockerSandboxService(
            sandbox_spec_service=mock_sandbox_spec_service,
            user_context=mock_user_context,
            db_session=db_session,
            container_name_prefix='oh-test-',
            host_port=3000,
            container_url_pattern='http://localhost:{port}',
            mounts=[],
            exposed_ports=[
                ExposedPort(
                    name=AGENT_SERVER, description='Agent server', container_port=8000
                ),
            ],
            health_check_path='/health',
            httpx_client=mock_httpx_client,
            max_num_sandboxes=3,
            extra_hosts={},
            docker_client=mock_docker_client,
        )

        with (
            patch.object(
                service_without_extra_hosts, '_find_unused_port', return_value=12345
            ),
            patch.object(
                service_without_extra_hosts, 'pause_old_sandboxes', return_value=[]
            ),
        ):
            # Execute
            await service_without_extra_hosts.start_sandbox()

        # Verify extra_hosts is None when empty dict is provided
        mock_docker_client.containers.run.assert_called_once()
        call_args = mock_docker_client.containers.run.call_args
        assert call_args[1]['extra_hosts'] is None

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_with_cors_origins(
        self,
        mock_urandom,
        mock_encodebytes,
        mock_sandbox_spec_service,
        mock_user_context,
        mock_httpx_client,
        mock_docker_client,
        db_session,
    ):
        """Test that CORS origins are set when web_url is configured."""
        # Setup
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']

        mock_container = MagicMock()
        mock_container.name = 'oh-test-test_container_id'
        mock_container.status = 'running'
        mock_container.labels = _labels('test-image:latest')
        mock_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {
                'Env': ['OH_SESSION_API_KEYS_0=test_session_key', 'TEST_VAR=test_value']
            },
            'NetworkSettings': {'Ports': {}},
        }
        mock_docker_client.containers.run.return_value = mock_container

        # Create service with web_url configured for CORS
        service_with_cors = DockerSandboxService(
            sandbox_spec_service=mock_sandbox_spec_service,
            user_context=mock_user_context,
            db_session=db_session,
            container_name_prefix='oh-test-',
            host_port=3000,
            container_url_pattern='http://192.168.1.100:{port}',
            mounts=[],
            exposed_ports=[
                ExposedPort(
                    name=AGENT_SERVER, description='Agent server', container_port=8000
                ),
            ],
            health_check_path='/health',
            httpx_client=mock_httpx_client,
            max_num_sandboxes=3,
            web_url='http://192.168.1.100:3000',
            docker_client=mock_docker_client,
        )

        with (
            patch.object(service_with_cors, '_find_unused_port', return_value=12345),
            patch.object(service_with_cors, 'pause_old_sandboxes', return_value=[]),
        ):
            # Execute
            await service_with_cors.start_sandbox()

        # Verify CORS origins environment variable was set
        mock_docker_client.containers.run.assert_called_once()
        call_args = mock_docker_client.containers.run.call_args
        env_vars = call_args[1]['environment']
        assert 'OH_ALLOW_CORS_ORIGINS_0' in env_vars
        assert env_vars['OH_ALLOW_CORS_ORIGINS_0'] == 'http://192.168.1.100:3000'

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_without_cors_origins(
        self,
        mock_urandom,
        mock_encodebytes,
        mock_sandbox_spec_service,
        mock_user_context,
        mock_httpx_client,
        mock_docker_client,
        db_session,
    ):
        """Test that CORS origins are not set when web_url is None."""
        # Setup
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']

        mock_container = MagicMock()
        mock_container.name = 'oh-test-test_container_id'
        mock_container.status = 'running'
        mock_container.labels = _labels('test-image:latest')
        mock_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {
                'Env': ['OH_SESSION_API_KEYS_0=test_session_key', 'TEST_VAR=test_value']
            },
            'NetworkSettings': {'Ports': {}},
        }
        mock_docker_client.containers.run.return_value = mock_container

        # Create service without web_url (local development mode)
        service_without_cors = DockerSandboxService(
            sandbox_spec_service=mock_sandbox_spec_service,
            user_context=mock_user_context,
            db_session=db_session,
            container_name_prefix='oh-test-',
            host_port=3000,
            container_url_pattern='http://localhost:{port}',
            mounts=[],
            exposed_ports=[
                ExposedPort(
                    name=AGENT_SERVER, description='Agent server', container_port=8000
                ),
            ],
            health_check_path='/health',
            httpx_client=mock_httpx_client,
            max_num_sandboxes=3,
            web_url=None,  # No web_url configured
            docker_client=mock_docker_client,
        )

        with (
            patch.object(service_without_cors, '_find_unused_port', return_value=12345),
            patch.object(service_without_cors, 'pause_old_sandboxes', return_value=[]),
        ):
            # Execute
            await service_without_cors.start_sandbox()

        # Verify CORS origins environment variable was NOT set
        mock_docker_client.containers.run.assert_called_once()
        call_args = mock_docker_client.containers.run.call_args
        env_vars = call_args[1]['environment']
        assert 'OH_ALLOW_CORS_ORIGINS_0' not in env_vars

    async def test_resume_sandbox_from_paused(self, service, store):
        """Test resuming a paused sandbox."""
        # Setup
        await store(_stored('oh-test-abc123'))
        mock_container = MagicMock()
        mock_container.status = 'paused'
        mock_container.labels = _labels()
        service.docker_client.containers.get.return_value = mock_container

        with patch.object(
            service, 'pause_old_sandboxes', return_value=[]
        ) as mock_cleanup:
            # Execute
            result = await service.resume_sandbox('oh-test-abc123')

        # Verify
        assert result is True
        mock_container.unpause.assert_called_once()
        mock_container.start.assert_not_called()
        # Verify cleanup was called with the correct limit
        mock_cleanup.assert_called_once_with(2)

    async def test_resume_sandbox_from_exited(self, service, store):
        """Test resuming an exited sandbox."""
        # Setup
        await store(_stored('oh-test-abc123'))
        mock_container = MagicMock()
        mock_container.status = 'exited'
        mock_container.labels = _labels()
        service.docker_client.containers.get.return_value = mock_container

        with patch.object(
            service, 'pause_old_sandboxes', return_value=[]
        ) as mock_cleanup:
            # Execute
            result = await service.resume_sandbox('oh-test-abc123')

        # Verify
        assert result is True
        mock_container.start.assert_called_once()
        mock_container.unpause.assert_not_called()
        # Verify cleanup was called with the correct limit
        mock_cleanup.assert_called_once_with(2)

    async def test_resume_sandbox_unmanaged_container(self, service):
        """Test resuming a container that this service has no record of."""
        # Setup
        mock_container = MagicMock()
        mock_container.status = 'paused'
        mock_container.labels = {}
        service.docker_client.containers.get.return_value = mock_container

        with patch.object(
            service, 'pause_old_sandboxes', return_value=[]
        ) as mock_cleanup:
            # Execute
            result = await service.resume_sandbox('oh-test-abc123')

        # Verify
        assert result is False
        mock_container.unpause.assert_not_called()
        # Verify cleanup was still called
        mock_cleanup.assert_called_once_with(2)

    async def test_resume_sandbox_not_found(self, service):
        """Test resuming non-existent sandbox."""
        # Setup
        service.docker_client.containers.get.side_effect = NotFound(
            'Container not found'
        )

        with patch.object(
            service, 'pause_old_sandboxes', return_value=[]
        ) as mock_cleanup:
            # Execute
            result = await service.resume_sandbox('oh-test-abc123')

        # Verify
        assert result is False
        # Verify cleanup was still called
        mock_cleanup.assert_called_once_with(2)

    async def test_pause_sandbox_success(self, service, store):
        """Test pausing a running sandbox."""
        # Setup
        await store(_stored('oh-test-abc123'))
        mock_container = MagicMock()
        mock_container.status = 'running'
        mock_container.labels = _labels()
        service.docker_client.containers.get.return_value = mock_container

        # Execute
        result = await service.pause_sandbox('oh-test-abc123')

        # Verify
        assert result is True
        mock_container.pause.assert_called_once()

    async def test_pause_sandbox_not_running(self, service, store):
        """Test pausing a non-running sandbox."""
        # Setup
        await store(_stored('oh-test-abc123'))
        mock_container = MagicMock()
        mock_container.status = 'paused'
        mock_container.labels = _labels()
        service.docker_client.containers.get.return_value = mock_container

        # Execute
        result = await service.pause_sandbox('oh-test-abc123')

        # Verify
        assert result is True
        mock_container.pause.assert_not_called()

    async def test_delete_sandbox_success(self, service, store):
        """Test successful sandbox deletion."""
        # Setup
        stored_sandbox = _stored('oh-test-abc123', session_api_key='session_key_123')
        await store(stored_sandbox)
        mock_container = MagicMock()
        mock_container.status = 'running'
        mock_container.labels = _labels()
        service.docker_client.containers.get.return_value = mock_container

        # Execute
        result = await service.delete_sandbox('oh-test-abc123')

        # Verify
        assert result is True
        mock_container.stop.assert_called_once_with(timeout=10)
        mock_container.remove.assert_called_once()
        assert stored_sandbox.deleted_at is not None
        assert stored_sandbox.session_api_key_hash is None

    async def test_delete_sandbox_hides_the_sandbox_afterwards(self, service, store):
        """Test that a deleted sandbox drops out of every read path."""
        # Setup
        await store(_stored('oh-test-abc123', session_api_key='session_key_123'))
        mock_container = MagicMock()
        mock_container.status = 'running'
        mock_container.labels = _labels()
        service.docker_client.containers.get.return_value = mock_container

        # Execute
        assert await service.delete_sandbox('oh-test-abc123') is True

        # Verify
        assert await service.get_sandbox('oh-test-abc123') is None
        assert (await service.search_sandboxes()).items == []
        assert (
            await service.get_sandbox_record_by_session_api_key('session_key_123')
        ) is None

    async def test_delete_sandbox_retires_a_row_whose_container_is_gone(
        self, service, store
    ):
        """Test that a row outliving its container is still retired."""
        # Setup
        stored_sandbox = _stored('oh-test-abc123')
        await store(stored_sandbox)
        service.docker_client.containers.get.side_effect = NotFound('gone')

        # Execute
        result = await service.delete_sandbox('oh-test-abc123')

        # Verify
        assert result is True
        assert stored_sandbox.deleted_at is not None

    async def test_delete_sandbox_already_stopped(self, service, store):
        """Test sandbox deletion when the container has already exited."""
        # Setup
        await store(_stored('oh-test-abc123'))
        mock_container = MagicMock()
        mock_container.status = 'exited'
        mock_container.labels = _labels()
        service.docker_client.containers.get.return_value = mock_container

        # Execute
        result = await service.delete_sandbox('oh-test-abc123')

        # Verify
        assert result is True
        mock_container.stop.assert_not_called()  # Already stopped
        mock_container.remove.assert_called_once()

    def test_find_unused_port(self, service):
        """Test finding an unused port."""
        # Execute
        port = service._find_unused_port()

        # Verify
        assert isinstance(port, int)
        assert 1024 <= port <= 65535

    def test_docker_status_to_sandbox_status(self, service):
        """Test Docker status to SandboxStatus conversion."""
        # Test all mappings
        assert (
            service._docker_status_to_sandbox_status('running') == SandboxStatus.RUNNING
        )
        assert (
            service._docker_status_to_sandbox_status('paused') == SandboxStatus.PAUSED
        )
        assert (
            service._docker_status_to_sandbox_status('exited') == SandboxStatus.PAUSED
        )
        assert (
            service._docker_status_to_sandbox_status('created')
            == SandboxStatus.STARTING
        )
        assert (
            service._docker_status_to_sandbox_status('restarting')
            == SandboxStatus.STARTING
        )
        assert (
            service._docker_status_to_sandbox_status('removing')
            == SandboxStatus.MISSING
        )
        assert service._docker_status_to_sandbox_status('dead') == SandboxStatus.ERROR
        assert (
            service._docker_status_to_sandbox_status('unknown') == SandboxStatus.ERROR
        )

    def test_get_container_env_vars(self, service):
        """Test environment variable extraction from container."""
        # Setup
        mock_container = MagicMock()
        mock_container.attrs = {
            'Config': {
                'Env': [
                    'VAR1=value1',
                    'VAR2=value2',
                    'VAR_NO_VALUE',
                    'VAR3=value=with=equals',
                ]
            }
        }

        # Execute
        result = service._get_container_env_vars(mock_container)

        # Verify
        assert result == {
            'VAR1': 'value1',
            'VAR2': 'value2',
            'VAR_NO_VALUE': None,
            'VAR3': 'value=with=equals',
        }

    async def test_to_sandbox_info_running(self, service, mock_running_container):
        """Test conversion of a row plus its running container."""
        # Execute
        result = await service._to_sandbox_info(
            _stored('oh-test-abc123'), mock_running_container
        )

        # Verify
        assert result is not None
        assert result.id == 'oh-test-abc123'
        assert result.created_by_user_id is None
        assert result.sandbox_spec_id == 'spec456'
        assert result.status == SandboxStatus.RUNNING
        assert result.session_api_key == 'session_key_123'
        assert len(result.exposed_urls) == 2

        # Check exposed URLs
        agent_url = next(url for url in result.exposed_urls if url.name == AGENT_SERVER)
        assert agent_url.url == 'http://localhost:12345'

        vscode_url = next(url for url in result.exposed_urls if url.name == VSCODE)
        assert (
            vscode_url.url
            == 'http://localhost:12346/?tkn=session_key_123&folder=/workspace'
        )

    async def test_to_sandbox_info_without_a_container_is_missing(self, service):
        """Test conversion of a row whose container is gone."""
        # Execute
        result = await service._to_sandbox_info(
            _stored('oh-test-abc123', created_by_user_id=OWNER_ID), None
        )

        # Verify
        assert result.id == 'oh-test-abc123'
        assert result.created_by_user_id == OWNER_ID
        assert result.status == SandboxStatus.MISSING
        assert result.created_at == CREATED_AT
        assert result.session_api_key is None
        assert result.exposed_urls is None

    async def test_to_sandbox_info_takes_created_at_from_the_row(
        self, service, mock_running_container
    ):
        """Test that the creation time is the row's, not the container's."""
        # Setup - the container's own timestamp is unparseable
        mock_running_container.attrs['Created'] = 'invalid-timestamp'
        created_at = datetime(2023, 6, 1, tzinfo=timezone.utc)

        # Execute
        result = await service._to_sandbox_info(
            _stored('oh-test-abc123', created_at=created_at), mock_running_container
        )

        # Verify
        assert result.created_at == created_at

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=True,
    )
    async def test_to_checked_sandbox_info_health_check_success(
        self, mock_is_docker, service, mock_running_container
    ):
        """Test health check success when running in Docker."""
        # Setup
        service.httpx_client.get.return_value.raise_for_status.return_value = None

        # Execute
        result = await service._to_checked_sandbox_info(
            _stored('oh-test-abc123'), mock_running_container
        )

        # Verify
        assert result is not None
        assert result.status == SandboxStatus.RUNNING
        assert result.exposed_urls is not None
        assert result.session_api_key == 'session_key_123'

        # Verify health check was called with Docker-internal URL
        service.httpx_client.get.assert_called_once_with(
            'http://host.docker.internal:12345/health'
        )

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=False,
    )
    async def test_to_checked_sandbox_info_health_check_success_not_in_docker(
        self, mock_is_docker, service, mock_running_container
    ):
        """Test health check success when not running in Docker."""
        # Setup
        service.httpx_client.get.return_value.raise_for_status.return_value = None

        # Execute
        result = await service._to_checked_sandbox_info(
            _stored('oh-test-abc123'), mock_running_container
        )

        # Verify
        assert result is not None
        assert result.status == SandboxStatus.RUNNING
        assert result.exposed_urls is not None
        assert result.session_api_key == 'session_key_123'

        # Verify health check was called with original localhost URL
        service.httpx_client.get.assert_called_once_with(
            'http://localhost:12345/health'
        )

    async def test_to_checked_sandbox_info_health_check_failure(
        self, service, mock_running_container
    ):
        """Test health check failure."""
        # Setup
        service.httpx_client.get.side_effect = httpx.HTTPError('Health check failed')

        # Execute
        result = await service._to_checked_sandbox_info(
            _stored('oh-test-abc123'), mock_running_container
        )

        # Verify
        assert result is not None
        assert result.status == SandboxStatus.ERROR
        assert result.exposed_urls is None
        assert result.session_api_key is None

    async def test_to_checked_sandbox_info_no_health_check(
        self, service, mock_running_container
    ):
        """Test when health check is disabled."""
        # Setup
        service.health_check_path = None

        # Execute
        result = await service._to_checked_sandbox_info(
            _stored('oh-test-abc123'), mock_running_container
        )

        # Verify
        assert result is not None
        assert result.status == SandboxStatus.RUNNING
        service.httpx_client.get.assert_not_called()

    async def test_to_checked_sandbox_info_no_exposed_urls(
        self, service, mock_paused_container
    ):
        """Test health check when no exposed URLs."""
        # Execute
        result = await service._to_checked_sandbox_info(
            _stored('oh-test-def456'), mock_paused_container
        )

        # Verify
        assert result is not None
        assert result.status == SandboxStatus.PAUSED
        service.httpx_client.get.assert_not_called()


class TestDockerSandboxServiceOwnership:
    """Test cases for ownership held in `v1_sandbox`."""

    @pytest.fixture
    def user_a_service(self, service):
        """Service acting for user-a."""
        service.user_context.get_user_id.return_value = 'user-a'
        return service

    @pytest.fixture
    async def user_b_container(self, store):
        """A running container owned by user-b, and its row."""
        await store(_stored('oh-test-userb', created_by_user_id='user-b'))
        container = MagicMock()
        container.name = 'oh-test-userb'
        container.status = 'running'
        container.labels = _labels('spec456', 'user-b')
        container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {'Env': [], 'WorkingDir': '/workspace'},
            'NetworkSettings': {'Ports': {}},
        }
        return container

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_writes_ownership_labels(
        self, mock_urandom, mock_encodebytes, service, mock_running_container
    ):
        """Test that start writes the spec, owner and managed marker labels."""
        # Setup
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']
        service.user_context.get_user_id.return_value = OWNER_ID
        service.docker_client.containers.run.return_value = mock_running_container

        with (
            patch.object(service, '_find_unused_port', side_effect=[12345, 12346]),
            patch.object(service, 'pause_old_sandboxes', return_value=[]),
        ):
            # Execute
            await service.start_sandbox()

        # Verify - the labels are still written, as the reconciliation tag
        labels = service.docker_client.containers.run.call_args[1]['labels']
        assert labels == {
            MANAGED_LABEL: 'true',
            SANDBOX_SPEC_ID_LABEL: 'test-image:latest',
            CREATED_BY_USER_ID_LABEL: OWNER_ID,
        }

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_writes_the_row_before_the_container(
        self,
        mock_urandom,
        mock_encodebytes,
        service,
        db_session,
        mock_running_container,
    ):
        """Test that the record exists by the time the container is created."""
        # Setup
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']
        service.user_context.get_user_id.return_value = OWNER_ID

        rows_at_create = []

        def _run(**kwargs):
            # Read the identity map rather than the database: this callback is
            # synchronous, so it cannot await IO of its own.
            rows_at_create.append(
                [
                    row
                    for row in db_session.sync_session.identity_map.values()
                    if isinstance(row, StoredSandbox)
                ]
            )
            return mock_running_container

        service.docker_client.containers.run.side_effect = _run

        with (
            patch.object(service, '_find_unused_port', side_effect=[12345, 12346]),
            patch.object(service, 'pause_old_sandboxes', return_value=[]),
        ):
            # Execute
            sandbox = await service.start_sandbox()

        # Verify
        assert sandbox.id == 'oh-test-test_container_id'
        # The row was flushed - it is in the identity map, not still pending.
        assert not db_session.sync_session.new
        (stored_sandbox,) = rows_at_create[0]
        assert stored_sandbox.id == 'oh-test-test_container_id'
        assert stored_sandbox.backend == DOCKER_BACKEND
        assert stored_sandbox.created_by_user_id == OWNER_ID
        assert stored_sandbox.sandbox_spec_id == 'test-image:latest'
        assert stored_sandbox.session_api_key_hash == hash_session_api_key(
            'test_session_key'
        )
        assert stored_sandbox.deleted_at is None

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_omits_owner_label_without_user_id(
        self, mock_urandom, mock_encodebytes, service, mock_running_container
    ):
        """Test that no owner label is written when there is no user id."""
        # Setup
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']
        service.docker_client.containers.run.return_value = mock_running_container

        with (
            patch.object(service, '_find_unused_port', side_effect=[12345, 12346]),
            patch.object(service, 'pause_old_sandboxes', return_value=[]),
        ):
            # Execute
            await service.start_sandbox()

        # Verify
        labels = service.docker_client.containers.run.call_args[1]['labels']
        assert CREATED_BY_USER_ID_LABEL not in labels
        assert labels[MANAGED_LABEL] == 'true'

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_honors_user_default_sandbox_spec(
        self,
        mock_urandom,
        mock_encodebytes,
        service,
        mock_sandbox_spec_service,
        mock_running_container,
    ):
        """Test that the user's default spec is resolved at start time."""
        # Setup
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']
        service.user_context.get_default_sandbox_spec_id.return_value = 'user-default'
        service.docker_client.containers.run.return_value = mock_running_container

        with (
            patch.object(service, '_find_unused_port', side_effect=[12345, 12346]),
            patch.object(service, 'pause_old_sandboxes', return_value=[]),
        ):
            # Execute
            await service.start_sandbox()

        # Verify
        mock_sandbox_spec_service.get_sandbox_spec.assert_called_once_with(
            'user-default'
        )
        mock_sandbox_spec_service.get_default_sandbox_spec.assert_not_called()

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_explicit_spec_beats_user_default(
        self,
        mock_urandom,
        mock_encodebytes,
        service,
        mock_sandbox_spec_service,
        mock_running_container,
    ):
        """Test that an explicit spec id wins over the user's default."""
        # Setup
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']
        service.user_context.get_default_sandbox_spec_id.return_value = 'user-default'
        service.docker_client.containers.run.return_value = mock_running_container

        with (
            patch.object(service, '_find_unused_port', side_effect=[12345, 12346]),
            patch.object(service, 'pause_old_sandboxes', return_value=[]),
        ):
            # Execute
            await service.start_sandbox(sandbox_spec_id='explicit-spec')

        # Verify
        mock_sandbox_spec_service.get_sandbox_spec.assert_called_once_with(
            'explicit-spec'
        )

    async def test_search_sandboxes_filters_by_owner(
        self, user_a_service, store, user_b_container, mock_running_container
    ):
        """Test that search returns the caller's sandboxes only."""
        # Setup - both containers are on the host; only one row is user-a's
        await store(_stored('oh-test-abc123', created_by_user_id='user-a'))
        user_a_service.docker_client.containers.list.return_value = [
            mock_running_container,
            user_b_container,
        ]
        user_a_service.httpx_client.get.return_value.raise_for_status.return_value = (
            None
        )

        # Execute
        result = await user_a_service.search_sandboxes()

        # Verify
        assert [item.id for item in result.items] == ['oh-test-abc123']

    async def test_start_sandbox_writes_the_owner_to_the_row(
        self, user_a_service, store, mock_running_container
    ):
        """Test that the row start_sandbox writes carries the caller."""
        # Setup
        user_a_service.docker_client.containers.run.return_value = (
            mock_running_container
        )

        with (
            patch.object(user_a_service, '_find_unused_port', side_effect=[1, 2]),
            patch.object(user_a_service, 'pause_old_sandboxes', return_value=[]),
        ):
            # Execute
            sandbox = await user_a_service.start_sandbox()

        # Verify - the sandbox reads back through the table, not the labels
        assert sandbox.created_by_user_id == 'user-a'
        user_a_service.docker_client.containers.get.return_value = (
            mock_running_container
        )
        read_back = await user_a_service.get_sandbox(sandbox.id)
        assert read_back is not None
        assert read_back.created_by_user_id == 'user-a'

    async def test_get_sandbox_hides_other_users_sandbox(
        self, user_a_service, user_b_container
    ):
        """Test that one user cannot read another user's sandbox."""
        # Setup
        user_a_service.docker_client.containers.get.return_value = user_b_container

        # Execute / Verify
        assert await user_a_service.get_sandbox('oh-test-userb') is None

    async def test_pause_rejects_other_users_sandbox(
        self, user_a_service, user_b_container
    ):
        """Test that one user cannot pause another user's sandbox."""
        # Setup
        user_a_service.docker_client.containers.get.return_value = user_b_container

        with patch.object(user_a_service, 'pause_old_sandboxes', return_value=[]):
            # Execute
            result = await user_a_service.pause_sandbox('oh-test-userb')

        # Verify
        assert result is False
        user_b_container.pause.assert_not_called()

    async def test_resume_rejects_other_users_sandbox(
        self, user_a_service, user_b_container
    ):
        """Test that one user cannot resume another user's sandbox."""
        # Setup
        user_b_container.status = 'paused'
        user_a_service.docker_client.containers.get.return_value = user_b_container

        with patch.object(user_a_service, 'pause_old_sandboxes', return_value=[]):
            # Execute
            result = await user_a_service.resume_sandbox('oh-test-userb')

        # Verify
        assert result is False
        user_b_container.unpause.assert_not_called()
        user_b_container.start.assert_not_called()

    async def test_delete_rejects_other_users_sandbox(
        self, user_a_service, user_b_container
    ):
        """Test that one user cannot delete another user's sandbox."""
        # Setup
        user_a_service.docker_client.containers.get.return_value = user_b_container

        # Execute
        result = await user_a_service.delete_sandbox('oh-test-userb')

        # Verify
        assert result is False
        user_b_container.stop.assert_not_called()
        user_b_container.remove.assert_not_called()

    async def test_session_api_key_lookup_hides_other_users_sandbox(
        self, user_a_service, store
    ):
        """Test that session key lookups are scoped to the caller."""
        # Setup - the key belongs to user-b
        await store(
            _stored(
                'oh-test-userb',
                created_by_user_id='user-b',
                session_api_key='user-b-key',
            )
        )

        # Execute / Verify
        assert (
            await user_a_service.get_sandbox_by_session_api_key('user-b-key')
        ) is None
        assert (
            await user_a_service.get_sandbox_record_by_session_api_key('user-b-key')
        ) is None

    async def test_session_api_key_record_makes_no_daemon_call(self, service, store):
        """Test that the webhook auth path never reaches the daemon."""
        # Setup
        await store(
            _stored(
                'oh-test-abc123',
                created_by_user_id='user-a',
                session_api_key='session_key_123',
            )
        )

        # Execute
        record = await service.get_sandbox_record_by_session_api_key('session_key_123')

        # Verify
        assert record is not None
        assert record.id == 'oh-test-abc123'
        service.docker_client.containers.list.assert_not_called()
        service.docker_client.containers.get.assert_not_called()

    async def test_admin_sees_all_managed_sandboxes(
        self, service, store, mock_running_container, user_b_container
    ):
        """Test that a caller with no user id sees every managed sandbox."""
        # Setup
        await store(_stored('oh-test-abc123', created_by_user_id='user-a'))
        service.docker_client.containers.list.return_value = [
            mock_running_container,
            user_b_container,
        ]
        service.httpx_client.get.return_value.raise_for_status.return_value = None

        # Execute
        result = await service.search_sandboxes()

        # Verify - no owner narrowing is applied
        assert {s.id for s in result.items} == {'oh-test-abc123', 'oh-test-userb'}
        assert {s.created_by_user_id for s in result.items} == {'user-a', 'user-b'}

    async def test_admin_can_get_any_managed_sandbox(self, service, user_b_container):
        """Test that a caller with no user id can read any managed sandbox."""
        # Setup
        service.docker_client.containers.get.return_value = user_b_container
        service.httpx_client.get.return_value.raise_for_status.return_value = None

        # Execute
        result = await service.get_sandbox('oh-test-userb')

        # Verify
        assert result is not None
        assert result.created_by_user_id == 'user-b'

    async def test_session_api_key_record_reports_owner(self, service, store):
        """Test that the session key record carries the owner from the row."""
        # Setup
        await store(
            _stored(
                'oh-test-abc123',
                created_by_user_id='user-a',
                session_api_key='session_key_123',
            )
        )

        # Execute
        record = await service.get_sandbox_record_by_session_api_key('session_key_123')

        # Verify
        assert record is not None
        assert record.id == 'oh-test-abc123'
        assert record.created_by_user_id == 'user-a'


class TestGetDockerClient:
    """Test cases for resolving the Docker daemon endpoint."""

    def test_docker_host_wins_when_set(self):
        """Test that DOCKER_HOST takes precedence over the CLI context."""
        with (
            patch.dict(os.environ, {'DOCKER_HOST': 'tcp://1.2.3.4:2375'}),
            patch('docker.from_env') as mock_from_env,
            patch('docker.DockerClient') as mock_client,
            patch(
                'openhands.app_server.sandbox.docker_sandbox_spec_service.ContextAPI'
            ) as mock_context_api,
        ):
            result = _connect_to_docker()

        assert result is mock_from_env.return_value
        mock_client.assert_not_called()
        mock_context_api.get_current_context.assert_not_called()

    def test_context_endpoint_used_when_docker_host_unset(self):
        """Test that the active CLI context endpoint is used without DOCKER_HOST."""
        context = MagicMock()
        context.Host = 'unix:///Users/me/.orbstack/run/docker.sock'

        with (
            patch.dict(os.environ, {}, clear=True),
            patch('docker.from_env') as mock_from_env,
            patch('docker.DockerClient') as mock_client,
            patch(
                'openhands.app_server.sandbox.docker_sandbox_spec_service.ContextAPI'
            ) as mock_context_api,
        ):
            mock_context_api.get_current_context.return_value = context
            result = _connect_to_docker()

        assert result is mock_client.return_value
        mock_client.assert_called_once_with(
            base_url='unix:///Users/me/.orbstack/run/docker.sock'
        )
        mock_from_env.assert_not_called()

    def test_falls_back_to_from_env_when_context_unavailable(self):
        """Test the fallback to the default socket when no context resolves."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch('docker.from_env') as mock_from_env,
            patch(
                'openhands.app_server.sandbox.docker_sandbox_spec_service.ContextAPI'
            ) as mock_context_api,
        ):
            mock_context_api.get_current_context.side_effect = Exception('no contexts')
            result = _connect_to_docker()

        assert result is mock_from_env.return_value

    def test_falls_back_to_from_env_when_context_endpoint_dead(self):
        """Test the fallback to the default socket when the context endpoint fails."""
        context = MagicMock()
        context.Host = 'unix:///nope/docker.sock'

        with (
            patch.dict(os.environ, {}, clear=True),
            patch('docker.from_env') as mock_from_env,
            patch(
                'docker.DockerClient', side_effect=DockerException('no such file')
            ) as mock_client,
            patch(
                'openhands.app_server.sandbox.docker_sandbox_spec_service.ContextAPI'
            ) as mock_context_api,
        ):
            mock_context_api.get_current_context.return_value = context
            result = _connect_to_docker()

        assert result is mock_from_env.return_value
        mock_client.assert_called_once_with(base_url='unix:///nope/docker.sock')

    def test_raises_actionable_error_when_nothing_connects(self):
        """Test that an unreachable daemon raises a SandboxError naming DOCKER_HOST."""
        context = MagicMock()
        context.Host = 'unix:///nope/docker.sock'

        with (
            patch.dict(os.environ, {}, clear=True),
            patch('docker.from_env', side_effect=DockerException('no socket')),
            patch('docker.DockerClient', side_effect=DockerException('no such file')),
            patch(
                'openhands.app_server.sandbox.docker_sandbox_spec_service.ContextAPI'
            ) as mock_context_api,
        ):
            mock_context_api.get_current_context.return_value = context
            with pytest.raises(SandboxError) as exc_info:
                _connect_to_docker()

        message = str(exc_info.value)
        assert 'DOCKER_HOST' in message
        assert 'unix:///nope/docker.sock' in message

    def test_client_is_memoized(self):
        """Test that the resolved client is reused across calls."""
        with patch(
            'openhands.app_server.sandbox.docker_sandbox_spec_service._connect_to_docker'
        ) as mock_connect:
            docker_sandbox_spec_service._global_docker_client = None
            try:
                first = get_docker_client()
                second = get_docker_client()
            finally:
                docker_sandbox_spec_service._global_docker_client = None

        assert first is second
        mock_connect.assert_called_once()


class TestVolumeMount:
    """Test cases for VolumeMount model."""

    def test_volume_mount_creation(self):
        """Test VolumeMount creation with default mode."""
        mount = VolumeMount(host_path='/host', container_path='/container')
        assert mount.host_path == '/host'
        assert mount.container_path == '/container'
        assert mount.mode == 'rw'

    def test_volume_mount_custom_mode(self):
        """Test VolumeMount creation with custom mode."""
        mount = VolumeMount(host_path='/host', container_path='/container', mode='ro')
        assert mount.mode == 'ro'

    def test_volume_mount_immutable(self):
        """Test that VolumeMount is immutable."""
        mount = VolumeMount(host_path='/host', container_path='/container')
        with pytest.raises(ValueError):  # Should raise validation error
            mount.host_path = '/new_host'


class TestExposedPort:
    """Test cases for ExposedPort model."""

    def test_exposed_port_creation(self):
        """Test ExposedPort creation with default port."""
        port = ExposedPort(name='test', description='Test port')
        assert port.name == 'test'
        assert port.description == 'Test port'
        assert port.container_port == 8000

    def test_exposed_port_custom_port(self):
        """Test ExposedPort creation with custom port."""
        port = ExposedPort(name='test', description='Test port', container_port=9000)
        assert port.container_port == 9000

    def test_exposed_port_immutable(self):
        """Test that ExposedPort is immutable."""
        port = ExposedPort(name='test', description='Test port')
        with pytest.raises(ValueError):  # Should raise validation error
            port.name = 'new_name'


class TestDockerSandboxServiceInjector:
    """Test cases for DockerSandboxServiceInjector configuration."""

    def test_default_values(self):
        """Test default configuration values."""
        from openhands.app_server.sandbox.docker_sandbox_service import (
            DockerSandboxServiceInjector,
        )

        injector = DockerSandboxServiceInjector()
        assert injector.host_port == 3000
        assert injector.container_url_pattern == 'http://localhost:{port}'

    def test_custom_host_port(self):
        """Test custom host_port configuration."""
        from openhands.app_server.sandbox.docker_sandbox_service import (
            DockerSandboxServiceInjector,
        )

        injector = DockerSandboxServiceInjector(host_port=4000)
        assert injector.host_port == 4000

    def test_custom_container_url_pattern(self):
        """Test custom container_url_pattern configuration."""
        from openhands.app_server.sandbox.docker_sandbox_service import (
            DockerSandboxServiceInjector,
        )

        injector = DockerSandboxServiceInjector(
            container_url_pattern='http://192.168.1.100:{port}'
        )
        assert injector.container_url_pattern == 'http://192.168.1.100:{port}'

    def test_custom_configuration_combined(self):
        """Test combined custom configuration for remote access."""
        from openhands.app_server.sandbox.docker_sandbox_service import (
            DockerSandboxServiceInjector,
        )

        injector = DockerSandboxServiceInjector(
            host_port=4000,
            container_url_pattern='http://192.168.1.100:{port}',
        )
        assert injector.host_port == 4000
        assert injector.container_url_pattern == 'http://192.168.1.100:{port}'

    def test_use_host_network_default_value(self):
        """Test that use_host_network field defaults to False."""
        from openhands.app_server.sandbox.docker_sandbox_service import (
            DockerSandboxServiceInjector,
        )

        injector = DockerSandboxServiceInjector()
        assert injector.use_host_network is False

    def test_use_host_network_can_be_enabled(self):
        """Test that use_host_network field can be set to True."""
        from openhands.app_server.sandbox.docker_sandbox_service import (
            DockerSandboxServiceInjector,
        )

        injector = DockerSandboxServiceInjector(use_host_network=True)
        assert injector.use_host_network is True

    def test_use_host_network_from_agent_server_env_var(self):
        """Test that AGENT_SERVER_USE_HOST_NETWORK env var enables host network mode."""
        import os
        from unittest.mock import patch

        from openhands.app_server.sandbox.docker_sandbox_service import (
            DockerSandboxServiceInjector,
        )

        env_vars = {
            'AGENT_SERVER_USE_HOST_NETWORK': 'true',
        }

        with patch.dict(os.environ, env_vars, clear=True):
            injector = DockerSandboxServiceInjector()
            assert injector.use_host_network is True

    def test_use_host_network_env_var_accepts_various_true_values(self):
        """Test that use_host_network accepts various truthy values."""
        import os
        from unittest.mock import patch

        from openhands.app_server.sandbox.docker_sandbox_service import (
            DockerSandboxServiceInjector,
        )

        for true_value in ['true', 'TRUE', 'True', '1', 'yes', 'YES', 'Yes']:
            env_vars = {'AGENT_SERVER_USE_HOST_NETWORK': true_value}
            with patch.dict(os.environ, env_vars, clear=True):
                injector = DockerSandboxServiceInjector()
                assert injector.use_host_network is True, (
                    f'Failed for value: {true_value}'
                )

    def test_use_host_network_env_var_defaults_to_false(self):
        """Test that unset or empty env var defaults to False."""
        import os
        from unittest.mock import patch

        from openhands.app_server.sandbox.docker_sandbox_service import (
            DockerSandboxServiceInjector,
        )

        # Empty environment
        with patch.dict(os.environ, {}, clear=True):
            injector = DockerSandboxServiceInjector()
            assert injector.use_host_network is False

        # Empty string
        with patch.dict(os.environ, {'AGENT_SERVER_USE_HOST_NETWORK': ''}, clear=True):
            injector = DockerSandboxServiceInjector()
            assert injector.use_host_network is False


class TestDockerSandboxServiceInjectorFromEnv:
    """Test cases for DockerSandboxServiceInjector environment variable configuration."""

    def test_config_from_env_with_sandbox_host_port(self):
        """Test that SANDBOX_HOST_PORT environment variable is respected."""
        import os
        from unittest.mock import patch

        env_vars = {
            'SANDBOX_HOST_PORT': '4000',
        }

        with patch.dict(os.environ, env_vars, clear=False):
            # Clear the global config to force reload
            import openhands.app_server.config as config_module
            from openhands.app_server.config import config_from_env

            config_module._global_config = None

            config = config_from_env()
            assert config.sandbox is not None
            assert config.sandbox.host_port == 4000

    def test_config_from_env_with_sandbox_container_url_pattern(self):
        """Test that SANDBOX_CONTAINER_URL_PATTERN environment variable is respected."""
        import os
        from unittest.mock import patch

        env_vars = {
            'SANDBOX_CONTAINER_URL_PATTERN': 'http://192.168.1.100:{port}',
        }

        with patch.dict(os.environ, env_vars, clear=False):
            # Clear the global config to force reload
            import openhands.app_server.config as config_module
            from openhands.app_server.config import config_from_env

            config_module._global_config = None

            config = config_from_env()
            assert config.sandbox is not None
            assert config.sandbox.container_url_pattern == 'http://192.168.1.100:{port}'

    def test_config_from_env_with_both_sandbox_vars(self):
        """Test that both SANDBOX_HOST_PORT and SANDBOX_CONTAINER_URL_PATTERN work together."""
        import os
        from unittest.mock import patch

        env_vars = {
            'SANDBOX_HOST_PORT': '4000',
            'SANDBOX_CONTAINER_URL_PATTERN': 'http://192.168.1.100:{port}',
        }

        with patch.dict(os.environ, env_vars, clear=False):
            # Clear the global config to force reload
            import openhands.app_server.config as config_module
            from openhands.app_server.config import config_from_env

            config_module._global_config = None

            config = config_from_env()
            assert config.sandbox is not None
            assert config.sandbox.host_port == 4000
            assert config.sandbox.container_url_pattern == 'http://192.168.1.100:{port}'


class TestDockerSandboxServiceHostNetwork:
    """Test cases for DockerSandboxService with host network mode."""

    @pytest.fixture
    def service_with_host_network(
        self,
        mock_sandbox_spec_service,
        mock_httpx_client,
        mock_docker_client,
        mock_user_context,
        db_session,
    ):
        """Create DockerSandboxService instance with host network enabled."""
        return DockerSandboxService(
            sandbox_spec_service=mock_sandbox_spec_service,
            user_context=mock_user_context,
            db_session=db_session,
            container_name_prefix='oh-test-',
            host_port=3000,
            container_url_pattern='http://localhost:{port}',
            mounts=[],
            exposed_ports=[
                ExposedPort(
                    name=AGENT_SERVER, description='Agent server', container_port=8000
                ),
                ExposedPort(
                    name=VSCODE, description='VSCode server', container_port=8001
                ),
            ],
            health_check_path='/health',
            httpx_client=mock_httpx_client,
            max_num_sandboxes=3,
            docker_client=mock_docker_client,
            use_host_network=True,
        )

    @pytest.fixture
    def mock_host_network_container(self):
        """Create a mock container running with host network mode."""
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
            'HostConfig': {
                'NetworkMode': 'host',
            },
            'NetworkSettings': {
                'Ports': None,
            },
        }
        return container

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_with_host_network(
        self, mock_urandom, mock_encodebytes, service_with_host_network
    ):
        """Test starting sandbox with host network mode."""
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']

        mock_container = MagicMock()
        mock_container.name = 'oh-test-test_container_id'
        mock_container.status = 'running'
        mock_container.labels = _labels('test-image:latest')
        mock_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {
                'Env': [
                    'OH_SESSION_API_KEYS_0=test_session_key',
                    'TEST_VAR=test_value',
                ],
                'WorkingDir': '/workspace',
            },
            'HostConfig': {'NetworkMode': 'host'},
            'NetworkSettings': {'Ports': None},
        }

        service_with_host_network.docker_client.containers.run.return_value = (
            mock_container
        )

        with patch.object(
            service_with_host_network, 'pause_old_sandboxes', return_value=[]
        ):
            result = await service_with_host_network.start_sandbox()

        assert result is not None
        assert result.id == 'oh-test-test_container_id'

        call_args = service_with_host_network.docker_client.containers.run.call_args
        assert call_args[1]['network_mode'] == 'host'
        assert call_args[1]['ports'] is None
        assert call_args[1]['extra_hosts'] is None

    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_host_network_uses_container_ports(
        self, mock_urandom, mock_encodebytes, service_with_host_network
    ):
        """Test that host network mode uses container ports directly in env vars."""
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']

        mock_container = MagicMock()
        mock_container.name = 'oh-test-test_container_id'
        mock_container.status = 'running'
        mock_container.labels = _labels('test-image:latest')
        mock_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {
                'Env': ['OH_SESSION_API_KEYS_0=test_session_key'],
                'WorkingDir': '/workspace',
            },
            'HostConfig': {'NetworkMode': 'host'},
            'NetworkSettings': {'Ports': None},
        }

        service_with_host_network.docker_client.containers.run.return_value = (
            mock_container
        )

        with patch.object(
            service_with_host_network, 'pause_old_sandboxes', return_value=[]
        ):
            await service_with_host_network.start_sandbox()

        call_args = service_with_host_network.docker_client.containers.run.call_args
        env_vars = call_args[1]['environment']
        assert env_vars[AGENT_SERVER] == '8000'
        assert env_vars[VSCODE] == '8001'

    async def test_to_sandbox_info_host_network(
        self, service_with_host_network, mock_host_network_container
    ):
        """Test conversion of host network container to SandboxInfo."""
        result = await service_with_host_network._to_sandbox_info(
            _stored('oh-test-abc123'), mock_host_network_container
        )

        assert result is not None
        assert result.id == 'oh-test-abc123'
        assert result.status == SandboxStatus.RUNNING
        assert result.session_api_key == 'session_key_123'
        assert len(result.exposed_urls) == 2

        agent_url = next(url for url in result.exposed_urls if url.name == AGENT_SERVER)
        assert agent_url.url == 'http://localhost:8000'
        assert agent_url.port == 8000

        vscode_url = next(url for url in result.exposed_urls if url.name == VSCODE)
        assert (
            vscode_url.url
            == 'http://localhost:8001/?tkn=session_key_123&folder=/workspace'
        )
        assert vscode_url.port == 8001

    @patch('openhands.app_server.sandbox.docker_sandbox_service._logger')
    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_host_network_warns_multiple_sandboxes(
        self,
        mock_urandom,
        mock_encodebytes,
        mock_logger,
        mock_sandbox_spec_service,
        mock_user_context,
        mock_httpx_client,
        mock_docker_client,
        db_session,
    ):
        """Test that warning is logged when use_host_network=True and max_num_sandboxes > 1."""
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']

        mock_container = MagicMock()
        mock_container.name = 'oh-test-test_container_id'
        mock_container.status = 'running'
        mock_container.labels = _labels('test-image:latest')
        mock_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {
                'Env': ['OH_SESSION_API_KEYS_0=test_session_key'],
                'WorkingDir': '/workspace',
            },
            'HostConfig': {'NetworkMode': 'host'},
            'NetworkSettings': {'Ports': None},
        }
        mock_docker_client.containers.run.return_value = mock_container

        # Create service with host network AND max_num_sandboxes > 1
        service = DockerSandboxService(
            sandbox_spec_service=mock_sandbox_spec_service,
            user_context=mock_user_context,
            db_session=db_session,
            container_name_prefix='oh-test-',
            host_port=3000,
            container_url_pattern='http://localhost:{port}',
            mounts=[],
            exposed_ports=[
                ExposedPort(
                    name=AGENT_SERVER, description='Agent server', container_port=8000
                ),
            ],
            health_check_path='/health',
            httpx_client=mock_httpx_client,
            max_num_sandboxes=3,  # > 1
            docker_client=mock_docker_client,
            use_host_network=True,
        )

        with patch.object(service, 'pause_old_sandboxes', return_value=[]):
            await service.start_sandbox()

        # Verify warning was logged about port collision risk
        mock_logger.warning.assert_called_once()
        warning_message = mock_logger.warning.call_args[0][0]
        assert (
            'Host network mode is enabled with max_num_sandboxes > 1' in warning_message
        )
        assert 'port collision' in warning_message.lower()

    @patch('openhands.app_server.sandbox.docker_sandbox_service._logger')
    @patch('openhands.app_server.sandbox.docker_sandbox_service.base62.encodebytes')
    @patch('os.urandom')
    async def test_start_sandbox_host_network_no_warning_single_sandbox(
        self,
        mock_urandom,
        mock_encodebytes,
        mock_logger,
        mock_sandbox_spec_service,
        mock_user_context,
        mock_httpx_client,
        mock_docker_client,
        db_session,
    ):
        """Test that no warning is logged when use_host_network=True and max_num_sandboxes=1."""
        mock_urandom.side_effect = [b'container_id', b'session_key']
        mock_encodebytes.side_effect = ['test_container_id', 'test_session_key']

        mock_container = MagicMock()
        mock_container.name = 'oh-test-test_container_id'
        mock_container.status = 'running'
        mock_container.labels = _labels('test-image:latest')
        mock_container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'Config': {
                'Env': ['OH_SESSION_API_KEYS_0=test_session_key'],
                'WorkingDir': '/workspace',
            },
            'HostConfig': {'NetworkMode': 'host'},
            'NetworkSettings': {'Ports': None},
        }
        mock_docker_client.containers.run.return_value = mock_container

        # Create service with host network AND max_num_sandboxes = 1
        service = DockerSandboxService(
            sandbox_spec_service=mock_sandbox_spec_service,
            user_context=mock_user_context,
            db_session=db_session,
            container_name_prefix='oh-test-',
            host_port=3000,
            container_url_pattern='http://localhost:{port}',
            mounts=[],
            exposed_ports=[
                ExposedPort(
                    name=AGENT_SERVER, description='Agent server', container_port=8000
                ),
            ],
            health_check_path='/health',
            httpx_client=mock_httpx_client,
            max_num_sandboxes=1,  # = 1, no warning expected
            docker_client=mock_docker_client,
            use_host_network=True,
        )

        with patch.object(service, 'pause_old_sandboxes', return_value=[]):
            await service.start_sandbox()

        # Verify no warning was logged about port collision
        mock_logger.warning.assert_not_called()

    @patch('openhands.app_server.sandbox.docker_sandbox_service.utc_now')
    async def test_to_checked_sandbox_info_uses_container_started_at(
        self, mock_utc_now, service
    ):
        """Test that health check uses container's StartedAt for grace period calculation instead of sandbox created_at.

        This tests the fix for the bug where resuming a stopped container incorrectly
        marked the sandbox as ERROR instead of STARTING because it was using
        sandbox_info.created_at (when the sandbox record was created) instead of
        the actual container start time.
        """
        # Setup - create a fresh container with State that includes StartedAt
        container = MagicMock()
        container.name = 'oh-test-abc123'
        container.status = 'running'
        container.labels = _labels('spec456')
        now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        mock_utc_now.return_value = now

        # Container started 4 seconds ago (within 15s grace period)
        container_started_within_grace_period = datetime(
            2024, 1, 15, 11, 59, 56, tzinfo=timezone.utc
        )
        # Sandbox was created 5 days ago (way outside grace period)
        sandbox_created_long_ago = datetime(2024, 1, 10, 10, 0, 0, tzinfo=timezone.utc)

        container.attrs = {
            'Created': '2024-01-15T10:30:00.000000000Z',
            'State': {'StartedAt': container_started_within_grace_period.isoformat()},
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

        # The row was written 5 days ago, so only the container's own start
        # time keeps this inside the grace period.
        stored_sandbox = _stored('oh-test-abc123', created_at=sandbox_created_long_ago)

        # Health check fails but container was started recently (within 15s grace period)
        service.httpx_client.get.side_effect = httpx.HTTPError('Health check failed')

        result = await service._to_checked_sandbox_info(stored_sandbox, container)

        # Verify - should be STARTING because container started within grace period
        assert result is not None
        assert result.status == SandboxStatus.STARTING
