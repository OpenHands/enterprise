"""Tests for utils/podman_utils.py and the generalized environment detection."""

from unittest.mock import patch

from openhands.app_server.utils.environment import (
    is_running_in_container,
    is_running_in_docker,
)
from openhands.app_server.utils.podman_utils import (
    PODMAN_HOST_GATEWAY,
    replace_localhost_hostname_for_podman,
)


class TestPodmanUtils:
    """Tests for replace_localhost_hostname_for_podman."""

    def test_replaces_localhost_when_in_container(self):
        with patch(
            'openhands.app_server.utils.podman_utils.is_running_in_container',
            return_value=True,
        ):
            result = replace_localhost_hostname_for_podman('http://localhost:3000/path')
            assert result == 'http://host.containers.internal:3000/path'

    def test_does_not_replace_when_not_in_container(self):
        with patch(
            'openhands.app_server.utils.podman_utils.is_running_in_container',
            return_value=False,
        ):
            result = replace_localhost_hostname_for_podman('http://localhost:3000/path')
            assert result == 'http://localhost:3000/path'

    def test_does_not_replace_non_localhost(self):
        with patch(
            'openhands.app_server.utils.podman_utils.is_running_in_container',
            return_value=True,
        ):
            result = replace_localhost_hostname_for_podman(
                'http://192.168.1.1:3000/path'
            )
            assert result == 'http://192.168.1.1:3000/path'

    def test_custom_replacement(self):
        with patch(
            'openhands.app_server.utils.podman_utils.is_running_in_container',
            return_value=True,
        ):
            result = replace_localhost_hostname_for_podman(
                'http://localhost:3000', replacement='my-host'
            )
            assert result == 'http://my-host:3000'

    def test_preserves_path_and_query(self):
        with patch(
            'openhands.app_server.utils.podman_utils.is_running_in_container',
            return_value=True,
        ):
            result = replace_localhost_hostname_for_podman(
                'http://localhost:3000/api/v1/webhooks?foo=bar'
            )
            assert result == (
                'http://host.containers.internal:3000/api/v1/webhooks?foo=bar'
            )

    def test_host_gateway_constant(self):
        assert PODMAN_HOST_GATEWAY == 'host.containers.internal'


class TestEnvironmentDetectionAlias:
    """Verify is_running_in_docker is an alias for is_running_in_container."""

    def test_alias_is_same_function(self):
        assert is_running_in_docker is is_running_in_container
