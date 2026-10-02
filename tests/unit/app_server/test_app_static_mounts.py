"""Static mount ordering for the canonical app (``openhands.app_server.app``).

``saas_server`` imports and extends this app, but ``app.py`` is also the
supported ASGI entrypoint on its own (``uvicorn openhands.app_server.app:app``,
re-exported by ``openhands.server.listen``). It carries its own copy of the
"mount ``/canvas`` ahead of the ``/`` catch-all" logic, guarded by
``os.path.isdir('./frontend/build/canvas')``. These tests pin both halves of
that logic directly against ``app.py`` rather than transitively through
``saas_server``.

The mount directories are hardcoded relative to the process working directory,
so each test builds a throwaway ``frontend/build`` tree under ``tmp_path`` and
runs the import in a subprocess with ``cwd=tmp_path``.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_ROUTE_PROBE = """
from starlette.routing import Match

from openhands.app_server.app import app

def matched_route(path):
    scope = {
        'type': 'http',
        'path': path,
        'root_path': '',
        'method': 'GET',
        'scheme': 'http',
        'server': ('testserver', 80),
        'client': ('testclient', 50000),
        'headers': [],
        'query_string': b'',
    }
    for route in app.router.routes:
        match, _ = route.matches(scope)
        if match != Match.NONE:
            return getattr(route, 'name', None)
    return None

print(f'RESULT /canvas/={matched_route("/canvas/")}')
print(f'RESULT /canvas/conversations={matched_route("/canvas/conversations")}')
print(f'RESULT /={matched_route("/")}')
"""


def _run_probe(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env['PYTHONPATH'] = f'{repo_root}:{env.get("PYTHONPATH", "")}'
    env['OPENHANDS_SUPPRESS_BANNER'] = '1'
    env['POSTHOG_CLIENT_KEY'] = 'test-posthog-key'
    env['SERVE_FRONTEND'] = 'true'

    # check=True is the assertion for the fail-soft contract: if the isdir
    # guard is dropped, StaticFiles validates a missing directory at import
    # time, raising RuntimeError and exiting the subprocess non-zero.
    result = subprocess.run(
        [sys.executable, '-c', textwrap.dedent(_ROUTE_PROBE)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return {
        line.removeprefix('RESULT ').split('=', 1)[0]: line.split('=', 1)[1]
        for line in result.stdout.splitlines()
        if line.startswith('RESULT ')
    }


def test_canvas_mounts_before_root_spa(tmp_path):
    """`/canvas` is served from frontend/build/canvas, ahead of the `/` catch-all."""
    frontend_build = tmp_path / 'frontend' / 'build'
    canvas_build = frontend_build / 'canvas'
    canvas_build.mkdir(parents=True)
    (frontend_build / 'index.html').write_text('<html>app</html>')
    (canvas_build / 'index.html').write_text('<html>canvas</html>')

    matches = _run_probe(tmp_path)

    assert matches['/canvas/'] == 'canvas'
    assert matches['/canvas/conversations'] == 'canvas'
    assert matches['/'] == 'dist'


def test_app_starts_when_canvas_bundle_absent(tmp_path):
    """No canvas bundle: app still imports, /canvas falls through to the '/' SPA."""
    frontend_build = tmp_path / 'frontend' / 'build'
    frontend_build.mkdir(parents=True)
    (frontend_build / 'index.html').write_text('<html>app</html>')
    # Deliberately do NOT create frontend/build/canvas.

    matches = _run_probe(tmp_path)

    assert matches['/canvas/'] == 'dist'
    assert matches['/'] == 'dist'
