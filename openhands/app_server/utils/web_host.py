"""Read the app's public hostname and URL from the environment.

``WEB_HOST`` is the bare hostname and ``OH_WEB_URL`` is the full URL. When both
are set, they must name the same host. A deployment can set only one of them.

These helpers read the environment directly, so module-level constants can use
them before the app server config loads.
"""

import os
from urllib.parse import urlparse


def get_web_url() -> str | None:
    """Return ``OH_WEB_URL`` without a trailing slash, or None when it is unset."""
    web_url = os.getenv('OH_WEB_URL', '').strip().rstrip('/')
    return web_url or None


def get_web_host() -> str | None:
    """Return ``WEB_HOST``, else the hostname in ``OH_WEB_URL``, else None."""
    web_host = os.getenv('WEB_HOST', '').strip()
    if web_host:
        return web_host
    web_url = get_web_url()
    if web_url:
        return urlparse(web_url).hostname
    return None
