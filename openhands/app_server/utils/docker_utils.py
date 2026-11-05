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


def sandbox_url_for_app_server(url: str) -> str:
    """Return the URL the app server uses to reach a sandbox URL.

    When the docker sandbox service sets internal_url (SANDBOX_INTERNAL_URL),
    that URL replaces the origin of container_url_pattern, so the request stays
    on the Docker network. Otherwise a localhost URL goes to
    host.docker.internal.
    """
    # The config imports the docker sandbox service, which imports this module.
    from openhands.app_server.config import get_global_config
    from openhands.app_server.sandbox.docker_sandbox_service import (
        DockerSandboxServiceInjector,
    )

    sandbox = get_global_config().sandbox
    if isinstance(sandbox, DockerSandboxServiceInjector) and sandbox.internal_url:
        pattern = urlsplit(sandbox.container_url_pattern)
        public_origin = f'{pattern.scheme}://{pattern.netloc}'
        if url == public_origin or url.startswith(f'{public_origin}/'):
            return sandbox.internal_url.rstrip('/') + url[len(public_origin) :]
    return replace_localhost_hostname_for_docker(url)
