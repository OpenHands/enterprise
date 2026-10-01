import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_static_member_routes_precede_member_detail(tmp_path):
    frontend_build = tmp_path / 'frontend' / 'build'
    frontend_build.mkdir(parents=True)
    (frontend_build / 'index.html').write_text('<html></html>')
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update(
        PYTHONPATH=f'{repo_root}:{env.get("PYTHONPATH", "")}',
        OPENHANDS_SUPPRESS_BANNER='1',
        POSTHOG_CLIENT_KEY='test-posthog-key',
        SERVE_FRONTEND='false',
        FRONTEND_DIRECTORY=str(frontend_build),
    )
    script = textwrap.dedent(
        """
        from starlette.routing import Match
        import saas_server

        prefix = '/api/organizations/11111111-1111-1111-1111-111111111111/members'
        cases = [
            ('GET', '/invite', 'list_pending_invitations'),
            ('POST', '/invite', 'create_invitation'),
            ('GET', '/22222222-2222-2222-2222-222222222222', 'get_org_member'),
        ]
        for method, suffix, expected in cases:
            scope = {
                'type': 'http', 'path': prefix + suffix, 'root_path': '',
                'method': method, 'scheme': 'http', 'headers': [],
                'query_string': b'', 'server': ('testserver', 80),
                'client': ('testclient', 50000),
            }
            route = next(
                route for route in saas_server.app.router.routes
                if route.matches(scope)[0] == Match.FULL
            )
            assert route.name == expected, (method, suffix, route.name)
        """
    )
    result = subprocess.run(
        [sys.executable, '-c', script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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


def test_saas_server_starts_when_canvas_bundle_absent(tmp_path):
    """No canvas bundle: app still imports, /canvas falls through to the '/' SPA.

    The ``os.path.isdir`` guard around the /canvas mount is load-bearing:
    ``StaticFiles`` validates its directory at construction time, so mounting a
    missing directory raises ``RuntimeError`` while ``saas_server`` is imported
    and the server never boots. This is the default state for anyone running the
    app without a canvas build, so the fail-soft behaviour must be pinned.
    """
    frontend_build = tmp_path / 'frontend' / 'build'
    frontend_build.mkdir(parents=True)
    (frontend_build / 'index.html').write_text('<html>app</html>')
    # Deliberately do NOT create frontend/build/canvas.

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
        print(f'RESULT /={matched_route("/")}')
        """
    )

    # check=True is the assertion: if the guard is dropped, importing
    # saas_server raises RuntimeError and this subprocess exits non-zero.
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

    # With no canvas bundle, /canvas has no dedicated mount and falls through
    # to the '/' catch-all SPA.
    assert matches['/canvas/'] == 'dist'
    assert matches['/'] == 'dist'
