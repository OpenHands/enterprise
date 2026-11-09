from urllib.parse import urlparse, urlunparse

from openhands.app_server.utils.environment import is_running_in_container

PODMAN_HOST_GATEWAY = 'host.containers.internal'


def replace_localhost_hostname_for_podman(
    url: str, replacement: str = PODMAN_HOST_GATEWAY
) -> str:
    """Replace localhost hostname in URL with host.containers.internal when
    running inside a Podman container.

    Podman rootless networking resolves ``host.containers.internal`` to the
    host automatically, so no ``extra_hosts`` mapping is needed.
    """
    if not is_running_in_container():
        return url
    parsed = urlparse(url)
    if parsed.hostname == 'localhost':
        netloc = parsed.netloc.replace('localhost', replacement, 1)
        return urlunparse(parsed._replace(netloc=netloc))
    return url
