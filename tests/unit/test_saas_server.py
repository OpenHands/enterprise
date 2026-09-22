import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_oauth_callback_route_precedes_spa_mount(tmp_path):
    frontend_build = tmp_path / 'frontend' / 'build'
    frontend_build.mkdir(parents=True)
    (frontend_build / 'index.html').write_text('<html></html>')

    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env['FRONTEND_DIRECTORY'] = str(frontend_build)
    env['PYTHONPATH'] = f'{repo_root}:{env.get("PYTHONPATH", "")}'
    env['OPENHANDS_SUPPRESS_BANNER'] = '1'
    env['POSTHOG_CLIENT_KEY'] = 'test-posthog-key'
    env['SERVE_FRONTEND'] = 'true'

    script = textwrap.dedent(
        """
        from starlette.routing import Match

        import saas_server

        scope = {
            'type': 'http',
            'path': '/oauth/keycloak/callback',
            'root_path': '',
            'method': 'GET',
            'scheme': 'http',
            'server': ('testserver', 80),
            'client': ('testclient', 50000),
            'headers': [],
            'query_string': b'',
        }

        for route in saas_server.app.router.routes:
            match, _ = route.matches(scope)
            if match != Match.NONE:
                print(getattr(route, 'name', None))
                break
        else:
            raise SystemExit('no route matched')
        """
    )

    result = subprocess.run(
        [sys.executable, '-c', script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    matched_route = result.stdout.strip().splitlines()[-1]

    assert matched_route == 'keycloak_callback'


def test_agent_canvas_mounts_before_root_spa(tmp_path):
    """`/canvas` is served from frontend/build/canvas, ahead of the `/` catch-all."""
    frontend_build = tmp_path / 'frontend' / 'build'
    canvas_build = frontend_build / 'canvas'
    canvas_build.mkdir(parents=True)
    (frontend_build / 'index.html').write_text('<html>app</html>')
    (canvas_build / 'index.html').write_text('<html>canvas</html>')

    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env['FRONTEND_DIRECTORY'] = str(frontend_build)
    env['PYTHONPATH'] = f'{repo_root}:{env.get("PYTHONPATH", "")}'
    env['OPENHANDS_SUPPRESS_BANNER'] = '1'
    env['POSTHOG_CLIENT_KEY'] = 'test-posthog-key'
    env['SERVE_FRONTEND'] = 'true'

    script = textwrap.dedent(
        """
        from starlette.routing import Match

        import saas_server

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
            for route in saas_server.app.router.routes:
                match, _ = route.matches(scope)
                if match != Match.NONE:
                    return getattr(route, 'name', None)
            return None

        print(f'RESULT /canvas/={matched_route("/canvas/")}')
        print(f'RESULT /canvas/conversations={matched_route("/canvas/conversations")}')
        print(f'RESULT /={matched_route("/")}')
        """
    )

    result = subprocess.run(
        [sys.executable, '-c', script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    matches = {
        line.removeprefix('RESULT ').split('=', 1)[0]: line.split('=', 1)[1]
        for line in result.stdout.splitlines()
        if line.startswith('RESULT ')
    }

    assert matches['/canvas/'] == 'canvas'
    assert matches['/canvas/conversations'] == 'canvas'
    assert matches['/'] == 'dist'
