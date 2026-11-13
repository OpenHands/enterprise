"""``_reattach_network`` against a real Docker daemon.

The other tests drive it with a MagicMock client. This one checks that the
docker-py calls it makes are accepted by a daemon, and that they bring a stopped
container back after Compose replaced its network.
"""

import uuid
from types import SimpleNamespace

import docker
import pytest

from openhands.app_server.sandbox.docker_sandbox_service import DockerSandboxService
from tests.postgres_testdb import IMAGE


@pytest.fixture
def docker_client():
    try:
        client = docker.from_env()
        client.ping()
    except docker.errors.DockerException:
        pytest.skip('no Docker daemon')
    yield client
    client.close()


def test_a_stopped_container_starts_on_the_recreated_network(docker_client):
    name = f'oh-test-reattach-{uuid.uuid4().hex[:8]}'
    old = docker_client.networks.create(name)
    container = docker_client.containers.create(
        IMAGE, entrypoint='sleep', command='300', network=name, name=name
    )
    networks = [old]
    try:
        container.start()
        container.stop(timeout=1)
        # What `docker compose down` then `up` does to the network.
        old.remove()
        new = docker_client.networks.create(name)
        networks.append(new)
        container.reload()
        with pytest.raises(docker.errors.APIError):
            container.start()

        service = SimpleNamespace(
            network=name, use_host_network=False, docker_client=docker_client
        )
        DockerSandboxService._reattach_network(service, container)
        container.start()

        container.reload()
        assert container.status == 'running'
        assert container.attrs['NetworkSettings']['Networks'][name]['NetworkID'] == (
            new.id
        )
    finally:
        container.remove(force=True)
        for network in networks:
            try:
                network.remove()
            except docker.errors.NotFound:
                pass
