"""In-process ASGI adapter for mounting Integrations Hub on saas_server.

Hub registers internal routes under ``/api/...`` and rewrites public paths when
``INTHUB_API_ROOT_PATH`` is set (default ``/api/integrations-hub``). Starlette
``Mount`` may strip the mount prefix before calling the child; some ASGI test
clients pass the full path. This wrapper normalizes to the full public path so
Hub's ``api_root_path`` middleware can rewrite to internal ``/api/...`` routes.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote_plus

from starlette.types import ASGIApp, Receive, Scope, Send

INTEGRATIONS_HUB_MOUNT_PATH = '/api/integrations-hub'


class PrefixedPathASGIApp:
    """Normalize ``scope['path']`` to the full public Hub API prefix."""

    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self.app = app
        self.prefix = prefix.rstrip('/') or '/'

    def _restore_path(self, path: str) -> str:
        if not path.startswith('/'):
            path = f'/{path}'
        if path == self.prefix or path.startswith(f'{self.prefix}/'):
            return path
        if path in ('', '/'):
            return self.prefix
        return f'{self.prefix}{path}'

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] in ('http', 'websocket'):
            scope = dict(scope)
            scope['path'] = self._restore_path(scope.get('path') or '')
            # Avoid double-prefixing via Starlette's root_path accounting.
            root = scope.get('root_path') or ''
            if root.endswith(self.prefix):
                scope['root_path'] = root[: -len(self.prefix)] or ''
        await self.app(scope, receive, send)


def resolve_shared_postgres_url() -> str | None:
    """Build a Postgres URL from OHE ``DB_*`` env vars (shared enterprise DB).

    Preference order for Hub:
    1. ``INTHUB_POSTGRES_URL`` (explicit override / tests)
    2. ``DB_HOST`` (+ ``DB_PORT`` / ``DB_NAME`` / ``DB_USER`` / ``DB_PASS``)
    """
    explicit = (os.getenv('INTHUB_POSTGRES_URL') or '').strip()
    if explicit:
        return explicit

    host = (os.getenv('DB_HOST') or '').strip()
    if not host:
        return None

    port = (os.getenv('DB_PORT') or '5432').strip() or '5432'
    name = (os.getenv('DB_NAME') or 'openhands').strip() or 'openhands'
    user = (os.getenv('DB_USER') or 'postgres').strip() or 'postgres'
    password = (os.getenv('DB_PASS') or 'postgres').strip()
    user_q = quote_plus(user)
    pass_q = quote_plus(password)
    return f'postgresql://{user_q}:{pass_q}@{host}:{port}/{name}'


def configure_integrations_hub_env() -> None:
    """Apply in-process defaults for Hub when the feature flag is on."""
    os.environ.setdefault('INTHUB_API_ROOT_PATH', INTEGRATIONS_HUB_MOUNT_PATH)
    shared = resolve_shared_postgres_url()
    if shared and not (os.getenv('INTHUB_POSTGRES_URL') or '').strip():
        # Point Hub at the shared OHE database without requiring a second URL.
        os.environ['INTHUB_POSTGRES_URL'] = shared


def create_integrations_hub_asgi() -> ASGIApp:
    """Return the Hub FastAPI app wrapped for mounting under the public prefix."""
    configure_integrations_hub_env()
    # Import after env defaults so Config picks up INTHUB_API_ROOT_PATH / DB.
    from integrations_hub.config import reset_config
    from integrations_hub.main import app as hub_app

    reset_config()
    return PrefixedPathASGIApp(hub_app, INTEGRATIONS_HUB_MOUNT_PATH)


def mount_integrations_hub(
    parent: Any,
    *,
    path: str = INTEGRATIONS_HUB_MOUNT_PATH,
) -> None:
    """Mount Integrations Hub on ``parent`` (FastAPI/Starlette app)."""
    parent.mount(path, create_integrations_hub_asgi())
