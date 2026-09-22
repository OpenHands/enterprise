from __future__ import annotations

import ipaddress
import socket
import urllib.parse
import urllib.request

from fastapi import HTTPException

from .config import get_default_config


_PRIVATE_HOSTNAMES = {"localhost", "localhost.localdomain"}


def _is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def _local_urls_allowed() -> bool:
    return get_default_config().allow_private_connector_urls


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):  # noqa: D401 - urllib hook
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(NoRedirectHandler)


def urlopen_no_redirect(request: urllib.request.Request, *, timeout: float):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def validate_external_url(url: str, *, purpose: str) -> str:
    """Validate an outbound URL before the backend fetches or calls it.

    Production connector traffic must use public HTTPS endpoints. Localhost and
    private-network targets are allowed only when INTHUB_DISABLE_AUTH=true so
    the test suite and local development can exercise ephemeral local servers.
    """
    parsed = urllib.parse.urlparse(str(url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail=f"Invalid {purpose} URL.")
    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=400,
            detail=f"{purpose} URL must not include credentials.",
        )
    allow_local = _local_urls_allowed()
    if parsed.scheme != "https" and not allow_local:
        raise HTTPException(
            status_code=400,
            detail=f"{purpose} URL must use https://.",
        )

    host = parsed.hostname.strip().lower()
    if host in _PRIVATE_HOSTNAMES and not allow_local:
        raise HTTPException(
            status_code=400,
            detail=f"{purpose} URL must not target localhost or private networks.",
        )

    try:
        if not _is_public_address(host) and not allow_local:
            raise HTTPException(
                status_code=400,
                detail=f"{purpose} URL must not target localhost or private networks.",
            )
        return str(url).strip()
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        # Let the actual outbound client surface transient DNS failures. The
        # security-critical cases are literal private IPs and resolvable private
        # hostnames, which we block here.
        return str(url).strip()

    for info in infos:
        address = info[4][0]
        if not _is_public_address(address) and not allow_local:
            raise HTTPException(
                status_code=400,
                detail=f"{purpose} URL must not resolve to localhost or private networks.",
            )
    return str(url).strip()
