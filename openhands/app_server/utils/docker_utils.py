from urllib.parse import urlparse, urlsplit, urlunparse

from openhands.app_server.utils.environment import is_running_in_container

# Backend constants imported lazily to avoid circular imports
_DOCKER_GATEWAY = 'host.docker.internal'
_PODMAN_GATEWAY = 'host.containers.internal'


def replace_localhost_hostname_for_docker(
    url: str, replacement: str = _DOCKER_GATEWAY
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
    if not is_running_in_container():
        return url
    parsed = urlparse(url)
    if parsed.hostname == 'localhost':
        # Replace only the hostname part, preserving port and everything else
        netloc = parsed.netloc.replace('localhost', replacement, 1)
        return urlunparse(parsed._replace(netloc=netloc))
    return url


def replace_localhost_hostname_for_backend(url: str, backend: str | None) -> str:
    """Replace localhost hostname in URL with the backend-appropriate gateway.

    Dispatches to ``host.containers.internal`` for Podman and
    ``host.docker.internal`` for everything else (including ``None``,
    which preserves the existing default behavior).
    """
    # Imported here to avoid a circular import at module load time
    from openhands.app_server.sandbox.sandbox_store import PODMAN_BACKEND

    if backend == PODMAN_BACKEND:
        return replace_localhost_hostname_for_docker(url, _PODMAN_GATEWAY)
    return replace_localhost_hostname_for_docker(url)


def sandbox_url_for_app_server(url: str, backend: str | None = None) -> str:
    """Return the URL the app server uses to reach a sandbox URL.

    Sandbox URLs carry the public address that browsers use. When the docker
    sandbox service sets internal_url (SANDBOX_INTERNAL_URL), that URL replaces
    the origin of container_url_pattern. Otherwise a localhost URL goes to
    the host gateway of ``backend``, which defaults to the configured sandbox's.
    """
    # The config imports the docker sandbox service, which imports this module.
    from openhands.app_server.config import get_global_config
    from openhands.app_server.sandbox.docker_sandbox_service import (
        DockerSandboxServiceInjector,
    )

    sandbox = get_global_config().sandbox
    backend = backend or getattr(sandbox, 'backend', None)
    if isinstance(sandbox, DockerSandboxServiceInjector) and sandbox.internal_url:
        pattern = urlsplit(sandbox.container_url_pattern)
        public_origin = f'{pattern.scheme}://{pattern.netloc}'
        if url == public_origin or url.startswith(f'{public_origin}/'):
            return sandbox.internal_url.rstrip('/') + url[len(public_origin) :]
    return replace_localhost_hostname_for_backend(url, backend)
