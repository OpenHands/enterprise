from urllib.parse import urlparse, urlsplit, urlunparse

from openhands.app_server.utils.environment import is_running_in_docker


def replace_localhost_hostname_for_docker(
    url: str, replacement: str = 'host.docker.internal'
) -> str:
    """Replace localhost hostname in URL with the specified replacement when running in Docker.

    This function only performs the replacement when the code is running inside a Docker
    container. When not running in Docker, it returns the original URL unchanged.

    Only replaces the hostname if it's exactly 'localhost', preserving all other
    parts of the URL including port, path, query parameters, etc.

    Args:
        url: The URL to process
        replacement: The hostname to replace localhost with (default: 'host.docker.internal')

    Returns:
        URL with localhost hostname replaced if running in Docker and hostname is localhost,
        otherwise returns the original URL unchanged
    """
    if not is_running_in_docker():
        return url
    parsed = urlparse(url)
    if parsed.hostname == 'localhost':
        # Replace only the hostname part, preserving port and everything else
        netloc = parsed.netloc.replace('localhost', replacement, 1)
        return urlunparse(parsed._replace(netloc=netloc))
    return url


_DEFAULT_PORTS = {'http': 80, 'https': 443}


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}'


def _replace_prefix(url: str, old: str, new: str) -> str | None:
    """Return url with the prefix old replaced by new, or None if old does not match."""
    old = old.rstrip('/')
    if url == old or url.startswith(f'{old}/'):
        return new.rstrip('/') + url[len(old) :]
    return None


def internal_sandbox_url(
    url: str, container_url_pattern: str, internal_url: str
) -> str | None:
    """Return a sandbox URL with internal_url in place of its public origin.

    The public origin is the origin of container_url_pattern. Returns None when
    the URL does not start with it.
    """
    return _replace_prefix(url, _origin(container_url_pattern), internal_url)


def sandbox_url_for_app_server(url: str) -> str:
    """Return the URL the app server uses to reach a sandbox URL.

    Sandbox URLs carry the public address that browsers use. When the docker
    sandbox service sets internal_url (SANDBOX_INTERNAL_URL), that URL replaces
    the origin of container_url_pattern. Otherwise a localhost URL goes to
    host.docker.internal.
    """
    # The config imports the docker sandbox service, which imports this module.
    from openhands.app_server.config import get_global_config
    from openhands.app_server.sandbox.docker_sandbox_service import (
        DockerSandboxServiceInjector,
    )

    sandbox = get_global_config().sandbox
    if isinstance(sandbox, DockerSandboxServiceInjector) and sandbox.internal_url:
        internal = internal_sandbox_url(
            url, sandbox.container_url_pattern, sandbox.internal_url
        )
        if internal is not None:
            return internal
    return replace_localhost_hostname_for_docker(url)


def sandbox_url_for_browser(url: str) -> str:
    """Return the public address of a sandbox URL.

    Responses to requests from sandboxes put SANDBOX_INTERNAL_URL in sandbox
    URLs. This puts the origin of container_url_pattern back.
    """
    from openhands.app_server.config import get_global_config
    from openhands.app_server.sandbox.docker_sandbox_service import (
        DockerSandboxServiceInjector,
    )

    sandbox = get_global_config().sandbox
    if isinstance(sandbox, DockerSandboxServiceInjector) and sandbox.internal_url:
        public = _replace_prefix(
            url, sandbox.internal_url, _origin(sandbox.container_url_pattern)
        )
        if public is not None:
            return public
    return url


def _host_and_port(url: str) -> tuple[str | None, int | None]:
    parts = urlsplit(url)
    return parts.hostname, parts.port or _DEFAULT_PORTS.get(parts.scheme)


def is_sandbox_web_url_host(
    host: str | None, sandbox_web_url: str | None, web_url: str | None
) -> bool:
    """Return True if a request with this Host header came in on sandbox_web_url.

    Returns False when sandbox_web_url has the same host and port as web_url,
    because browsers then use that address too.
    """
    if not host or not sandbox_web_url:
        return False
    try:
        sandbox_address = _host_and_port(sandbox_web_url)
        if web_url and _host_and_port(web_url) == sandbox_address:
            return False
        scheme = urlsplit(sandbox_web_url).scheme
        return _host_and_port(f'{scheme}://{host}') == sandbox_address
    except ValueError:
        # The Host header has a port that is not a number.
        return False
