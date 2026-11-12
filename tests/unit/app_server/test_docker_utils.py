from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openhands.app_server.sandbox.docker_sandbox_service import (
    DockerSandboxServiceInjector,
)
from openhands.app_server.utils.docker_utils import (
    is_sandbox_web_url_host,
    replace_localhost_hostname_for_docker,
    sandbox_url_for_app_server,
    sandbox_url_for_browser,
)


class TestReplaceLocalhostHostnameForDocker:
    """Test cases for replace_localhost_hostname_for_docker function."""

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=True,
    )
    def test_replace_localhost_basic_in_docker(self, mock_is_docker):
        """Test basic localhost replacement when running in Docker."""
        # Basic HTTP URL
        result = replace_localhost_hostname_for_docker('http://localhost:8080')
        assert result == 'http://host.docker.internal:8080'

        # HTTPS URL
        result = replace_localhost_hostname_for_docker('https://localhost:443')
        assert result == 'https://host.docker.internal:443'

        # No port specified
        result = replace_localhost_hostname_for_docker('http://localhost')
        assert result == 'http://host.docker.internal'

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=False,
    )
    def test_replace_localhost_basic_not_in_docker(self, mock_is_docker):
        """Test that localhost is NOT replaced when not running in Docker."""
        # Basic HTTP URL
        result = replace_localhost_hostname_for_docker('http://localhost:8080')
        assert result == 'http://localhost:8080'

        # HTTPS URL
        result = replace_localhost_hostname_for_docker('https://localhost:443')
        assert result == 'https://localhost:443'

        # No port specified
        result = replace_localhost_hostname_for_docker('http://localhost')
        assert result == 'http://localhost'

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=True,
    )
    def test_replace_localhost_with_path_and_query(self, mock_is_docker):
        """Test localhost replacement preserving path and query parameters."""
        # With path
        result = replace_localhost_hostname_for_docker(
            'http://localhost:3000/api/health'
        )
        assert result == 'http://host.docker.internal:3000/api/health'

        # With query parameters containing localhost
        result = replace_localhost_hostname_for_docker(
            'http://localhost:8080/path?param=localhost&other=value'
        )
        assert (
            result
            == 'http://host.docker.internal:8080/path?param=localhost&other=value'
        )

        # With path containing localhost
        result = replace_localhost_hostname_for_docker(
            'http://localhost:9000/localhost/endpoint'
        )
        assert result == 'http://host.docker.internal:9000/localhost/endpoint'

        # With fragment
        result = replace_localhost_hostname_for_docker(
            'http://localhost:8080/path#localhost'
        )
        assert result == 'http://host.docker.internal:8080/path#localhost'

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=True,
    )
    def test_replace_localhost_with_authentication(self, mock_is_docker):
        """Test localhost replacement with authentication in URL."""
        result = replace_localhost_hostname_for_docker(
            'http://user:pass@localhost:8080/path'
        )
        assert result == 'http://user:pass@host.docker.internal:8080/path'

        result = replace_localhost_hostname_for_docker(
            'https://admin:secret@localhost:443/admin'
        )
        assert result == 'https://admin:secret@host.docker.internal:443/admin'

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=True,
    )
    def test_replace_localhost_different_protocols(self, mock_is_docker):
        """Test localhost replacement with different protocols."""
        # FTP
        result = replace_localhost_hostname_for_docker('ftp://localhost:21/files')
        assert result == 'ftp://host.docker.internal:21/files'

        # WebSocket
        result = replace_localhost_hostname_for_docker('ws://localhost:8080/socket')
        assert result == 'ws://host.docker.internal:8080/socket'

        # WebSocket Secure
        result = replace_localhost_hostname_for_docker(
            'wss://localhost:443/secure-socket'
        )
        assert result == 'wss://host.docker.internal:443/secure-socket'

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=True,
    )
    def test_no_replacement_for_non_localhost(self, mock_is_docker):
        """Test that non-localhost hostnames are not replaced even when in Docker."""
        # IP address
        result = replace_localhost_hostname_for_docker('http://127.0.0.1:8080')
        assert result == 'http://127.0.0.1:8080'

        # Different hostname
        result = replace_localhost_hostname_for_docker('http://example.com:8080')
        assert result == 'http://example.com:8080'

        # Hostname containing localhost but not exact match
        result = replace_localhost_hostname_for_docker('http://mylocalhost:8080')
        assert result == 'http://mylocalhost:8080'

        # Subdomain of localhost
        result = replace_localhost_hostname_for_docker('http://api.localhost:8080')
        assert result == 'http://api.localhost:8080'

        # localhost as subdomain
        result = replace_localhost_hostname_for_docker(
            'http://localhost.example.com:8080'
        )
        assert result == 'http://localhost.example.com:8080'

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=True,
    )
    def test_custom_replacement_hostname(self, mock_is_docker):
        """Test using custom replacement hostname."""
        result = replace_localhost_hostname_for_docker(
            'http://localhost:8080', 'custom.host'
        )
        assert result == 'http://custom.host:8080'

        result = replace_localhost_hostname_for_docker(
            'https://localhost:443/path', 'internal.docker'
        )
        assert result == 'https://internal.docker:443/path'

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=True,
    )
    def test_edge_cases_in_docker(self, mock_is_docker):
        """Test edge cases and malformed URLs when in Docker."""
        # Empty string
        result = replace_localhost_hostname_for_docker('')
        assert result == ''

        # Malformed URL (no protocol)
        result = replace_localhost_hostname_for_docker('localhost:8080')
        assert result == 'localhost:8080'

        # Just hostname
        result = replace_localhost_hostname_for_docker('localhost')
        assert result == 'localhost'

        # URL with no hostname
        result = replace_localhost_hostname_for_docker('http://:8080/path')
        assert result == 'http://:8080/path'

        # Invalid URL structure
        result = replace_localhost_hostname_for_docker('not-a-url')
        assert result == 'not-a-url'

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=False,
    )
    def test_edge_cases_not_in_docker(self, mock_is_docker):
        """Test edge cases and malformed URLs when not in Docker."""
        # Empty string
        result = replace_localhost_hostname_for_docker('')
        assert result == ''

        # Malformed URL (no protocol)
        result = replace_localhost_hostname_for_docker('localhost:8080')
        assert result == 'localhost:8080'

        # Just hostname
        result = replace_localhost_hostname_for_docker('localhost')
        assert result == 'localhost'

        # URL with no hostname
        result = replace_localhost_hostname_for_docker('http://:8080/path')
        assert result == 'http://:8080/path'

        # Invalid URL structure
        result = replace_localhost_hostname_for_docker('not-a-url')
        assert result == 'not-a-url'

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=True,
    )
    def test_complex_urls(self, mock_is_docker):
        """Test complex URL scenarios."""
        # Multiple query parameters and fragments
        complex_url = 'http://localhost:8080/api/v1/health?timeout=30&retry=3&host=localhost#section'
        result = replace_localhost_hostname_for_docker(complex_url)
        expected = 'http://host.docker.internal:8080/api/v1/health?timeout=30&retry=3&host=localhost#section'
        assert result == expected

        # URL with encoded characters
        encoded_url = (
            'http://localhost:8080/path%20with%20spaces?param=value%20with%20spaces'
        )
        result = replace_localhost_hostname_for_docker(encoded_url)
        expected = 'http://host.docker.internal:8080/path%20with%20spaces?param=value%20with%20spaces'
        assert result == expected

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=True,
    )
    def test_integration_with_docker_detection_in_docker(self, mock_is_docker):
        """Test integration scenario similar to actual usage when in Docker."""
        # Simulate the actual usage pattern in the code
        app_server_url = 'http://localhost:35375'

        # This is how it's used in the actual code
        internal_url = replace_localhost_hostname_for_docker(app_server_url)

        assert internal_url == 'http://host.docker.internal:35375'

        # Test with health check path appended
        health_check_url = f'{internal_url}/health'
        assert health_check_url == 'http://host.docker.internal:35375/health'

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=False,
    )
    def test_integration_with_docker_detection_not_in_docker(self, mock_is_docker):
        """Test integration scenario similar to actual usage when not in Docker."""
        # Simulate the actual usage pattern in the code
        app_server_url = 'http://localhost:35375'

        # This is how it's used in the actual code
        internal_url = replace_localhost_hostname_for_docker(app_server_url)

        # Should return original URL when not in Docker
        assert internal_url == 'http://localhost:35375'

        # Test with health check path appended
        health_check_url = f'{internal_url}/health'
        assert health_check_url == 'http://localhost:35375/health'

    @patch(
        'openhands.app_server.utils.docker_utils.is_running_in_docker',
        return_value=True,
    )
    def test_preserves_original_url_structure(self, mock_is_docker):
        """Test that all URL components are preserved correctly."""
        original_url = 'https://user:pass@localhost:8443/api/v1/endpoint?param1=value1&param2=value2#fragment'
        result = replace_localhost_hostname_for_docker(original_url)
        expected = 'https://user:pass@host.docker.internal:8443/api/v1/endpoint?param1=value1&param2=value2#fragment'

        assert result == expected

        # Verify each component is preserved
        from urllib.parse import urlparse

        original_parsed = urlparse(original_url)
        result_parsed = urlparse(result)

        assert original_parsed.scheme == result_parsed.scheme
        assert original_parsed.username == result_parsed.username
        assert original_parsed.password == result_parsed.password
        assert original_parsed.port == result_parsed.port
        assert original_parsed.path == result_parsed.path
        assert original_parsed.query == result_parsed.query
        assert original_parsed.fragment == result_parsed.fragment

        # Only hostname should be different
        assert original_parsed.hostname == 'localhost'
        assert result_parsed.hostname == 'host.docker.internal'


def _config(sandbox):
    return patch(
        'openhands.app_server.config.get_global_config',
        return_value=SimpleNamespace(sandbox=sandbox),
    )


def _docker_sandbox(container_url_pattern: str, internal_url: str | None):
    return DockerSandboxServiceInjector(
        container_url_pattern=container_url_pattern,
        internal_url=internal_url,
        network=None,
    )


@patch(
    'openhands.app_server.utils.docker_utils.is_running_in_docker',
    return_value=True,
)
class TestSandboxUrlForAppServer:
    """Test cases for sandbox_url_for_app_server function."""

    @pytest.mark.parametrize(
        ('pattern', 'internal_url', 'url', 'expected'),
        [
            (
                'http://localhost:9043/runtime/{sandbox_id}/{port}',
                'http://proxy',
                'http://localhost:9043/runtime/oh-agent-server-abc/8000',
                'http://proxy/runtime/oh-agent-server-abc/8000',
            ),
            (
                'http://localhost:9043/runtime/{sandbox_id}/{port}',
                'http://proxy/',
                'http://localhost:9043/runtime/oh-agent-server-abc/8000/api/x?y=1',
                'http://proxy/runtime/oh-agent-server-abc/8000/api/x?y=1',
            ),
            (
                'https://oh.example.com/runtime/{sandbox_id}/{port}',
                'http://proxy',
                'https://oh.example.com/runtime/oh-agent-server-abc/8000',
                'http://proxy/runtime/oh-agent-server-abc/8000',
            ),
        ],
    )
    def test_internal_url_replaces_the_public_origin(
        self, mock_is_docker, pattern, internal_url, url, expected
    ):
        with _config(_docker_sandbox(pattern, internal_url)):
            assert sandbox_url_for_app_server(url) == expected

    def test_other_origins_are_not_rewritten(self, mock_is_docker):
        """A URL that does not start with the public origin keeps its own origin."""
        sandbox = _docker_sandbox(
            'http://localhost:9043/runtime/{sandbox_id}/{port}', 'http://proxy'
        )
        with _config(sandbox):
            assert (
                sandbox_url_for_app_server('http://localhost:90431/runtime/x/8000')
                == 'http://host.docker.internal:90431/runtime/x/8000'
            )
            assert (
                sandbox_url_for_app_server('https://other.example.com/x')
                == 'https://other.example.com/x'
            )

    def test_without_internal_url_localhost_goes_to_the_docker_host(
        self, mock_is_docker
    ):
        sandbox = _docker_sandbox(
            'http://localhost:9043/runtime/{sandbox_id}/{port}', None
        )
        with _config(sandbox):
            assert (
                sandbox_url_for_app_server('http://localhost:9043/runtime/x/8000')
                == 'http://host.docker.internal:9043/runtime/x/8000'
            )

    def test_other_sandbox_services_use_the_localhost_rewrite(self, mock_is_docker):
        with _config(SimpleNamespace(internal_url='http://proxy')):
            assert (
                sandbox_url_for_app_server('http://localhost:3000/x')
                == 'http://host.docker.internal:3000/x'
            )

    def test_internal_urls_are_not_rewritten(self, mock_is_docker):
        """A URL that already uses the internal URL reaches the sandbox as is."""
        sandbox = _docker_sandbox(
            'http://localhost:9043/runtime/{sandbox_id}/{port}', 'http://proxy'
        )
        with _config(sandbox):
            assert (
                sandbox_url_for_app_server('http://proxy/runtime/x/8000/health')
                == 'http://proxy/runtime/x/8000/health'
            )


class TestSandboxUrlForBrowser:
    """Test cases for sandbox_url_for_browser function."""

    @pytest.mark.parametrize(
        ('internal_url', 'url', 'expected'),
        [
            (
                'http://proxy',
                'http://proxy/runtime/oh-agent-server-abc/8011',
                'http://localhost:9043/runtime/oh-agent-server-abc/8011',
            ),
            (
                'http://proxy/',
                'http://proxy/runtime/oh-agent-server-abc/8001/?tkn=k&folder=/w',
                'http://localhost:9043/runtime/oh-agent-server-abc/8001/?tkn=k&folder=/w',
            ),
            (
                'http://proxy',
                'http://localhost:9043/runtime/oh-agent-server-abc/8011',
                'http://localhost:9043/runtime/oh-agent-server-abc/8011',
            ),
            (
                'http://proxy',
                'http://proxy2/runtime/oh-agent-server-abc/8011',
                'http://proxy2/runtime/oh-agent-server-abc/8011',
            ),
        ],
    )
    def test_internal_url_goes_back_to_the_public_origin(
        self, internal_url, url, expected
    ):
        sandbox = _docker_sandbox(
            'http://localhost:9043/runtime/{sandbox_id}/{port}', internal_url
        )
        with _config(sandbox):
            assert sandbox_url_for_browser(url) == expected

    def test_without_internal_url_nothing_changes(self):
        sandbox = _docker_sandbox('http://localhost:{port}', None)
        with _config(sandbox):
            assert sandbox_url_for_browser('http://proxy/x') == 'http://proxy/x'
        with _config(SimpleNamespace(internal_url='http://proxy')):
            assert sandbox_url_for_browser('http://proxy/x') == 'http://proxy/x'


class TestIsSandboxWebUrlHost:
    """Test cases for is_sandbox_web_url_host function."""

    @pytest.mark.parametrize(
        ('host', 'sandbox_web_url', 'web_url', 'expected'),
        [
            ('proxy', 'http://proxy', 'http://localhost:9043', True),
            ('proxy:80', 'http://proxy', 'http://localhost:9043', True),
            ('PROXY', 'http://proxy/', 'http://localhost:9043', True),
            ('proxy', 'http://proxy:80', 'http://localhost:9043', True),
            ('oh.internal', 'https://oh.internal', 'https://oh.example.com', True),
            ('oh.internal:443', 'https://oh.internal', 'https://oh.example.com', True),
            ('localhost:9043', 'http://proxy', 'http://localhost:9043', False),
            ('proxy:8080', 'http://proxy', 'http://localhost:9043', False),
            ('app:3000', 'http://proxy', 'http://localhost:9043', False),
            ('proxy:abc', 'http://proxy', 'http://localhost:9043', False),
            (None, 'http://proxy', 'http://localhost:9043', False),
            ('proxy', None, 'http://localhost:9043', False),
            ('proxy', 'http://proxy', None, True),
        ],
    )
    def test_matches_the_sandbox_web_url_host(
        self, host, sandbox_web_url, web_url, expected
    ):
        assert is_sandbox_web_url_host(host, sandbox_web_url, web_url) is expected

    def test_shared_address_is_not_a_sandbox_request(self):
        """When browsers and sandboxes use the same address, links stay public."""
        assert not is_sandbox_web_url_host(
            'oh.example.com', 'https://oh.example.com', 'https://oh.example.com/'
        )
