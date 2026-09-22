"""Static file serving for SPA mode deployments.

When INTHUB_STATIC_DIR is set, FastAPI serves the pre-built Next.js SPA.

Usage:
    INTHUB_STATIC_DIR=/app/out uvicorn integrations_hub.main:app
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from .config import get_default_config


logger = logging.getLogger(__name__)


class HubStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict) -> Response:
        response = await super().get_response(path, scope)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


def mount_static_files(app: FastAPI) -> bool:
    """Mount static file serving if INTHUB_STATIC_DIR is configured.

    Uses FastAPI's StaticFiles with html=True for SPA-style routing. The mount
    follows INTHUB_ROOT_PATH when configured, matching the base path baked into
    the Kubernetes SPA image.
    """
    config = get_default_config()
    static_dir = config.static_dir

    if not static_dir:
        return False

    static_path = Path(static_dir)
    if not static_path.exists() or not static_path.is_dir():
        logger.warning("INTHUB_STATIC_DIR=%s is not a valid directory", static_dir)
        return False

    mount_path = config.root_path or "/"
    logger.info("Mounting static files from %s at %s", static_dir, mount_path)

    # Mount the entire static directory with html=True for SPA routing
    # This automatically serves index.html for directories
    app.mount(
        mount_path,
        HubStaticFiles(directory=static_dir, html=True),
        name="integrations-hub-spa",
    )

    return True
