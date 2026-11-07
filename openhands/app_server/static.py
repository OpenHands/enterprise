import json
import os

from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

AGENT_CANVAS_MANIFEST = 'agent-canvas-build.json'


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except Exception:
            # FIXME: just making this HTTPException doesn't work for some reason
            return await super().get_response('index.html', scope)


def describe_agent_canvas_bundle(directory: str) -> str:
    """Say which Agent Canvas build this app serves at /canvas, if any.

    The manifest is written by frontend/scripts/build-agent-canvas.sh.
    """
    if not os.path.isdir(directory):
        return (
            f'No Agent Canvas bundle at {directory}: this app does not serve '
            '/canvas, so a separate Agent Canvas service must route it'
        )
    try:
        with open(os.path.join(directory, AGENT_CANVAS_MANIFEST)) as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        manifest = None
    if not isinstance(manifest, dict):
        return (
            f'Serving an Agent Canvas bundle of unknown ref at /canvas from '
            f'{directory} (no readable {AGENT_CANVAS_MANIFEST})'
        )
    return (
        f'Serving Agent Canvas ref={manifest.get("ref")} '
        f'commit={manifest.get("commit") or "unknown"} at /canvas from {directory}'
    )
