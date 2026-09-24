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
