import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    'keycloak,client_id,app_id,private_key,webhooks',
    [
        ('false', 'oauth-client', '', '', False),
        ('false', '', '12345', '', False),
        ('false', '', '12345', 'test-signing-key', True),
        ('false', 'app-client', '', 'test-signing-key', True),
        ('true', 'app-client', '', 'test-signing-key', True),
        ('true', '', '12345', 'test-signing-key', False),
    ],
)
def test_github_webhook_registration_requires_native_app_signing_config(
    tmp_path: Path,
    keycloak: str,
    client_id: str,
    app_id: str,
    private_key: str,
    webhooks: bool,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    frontend_build = tmp_path / 'frontend' / 'build'
    frontend_build.mkdir(parents=True)
    (frontend_build / 'index.html').write_text('<html></html>')
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            'PATH',
            'HOME',
            'USERPROFILE',
            'SYSTEMROOT',
            'WINDIR',
            'APPDATA',
            'LOCALAPPDATA',
            'TMPDIR',
            'TEMP',
            'TMP',
            'LANG',
            'LC_ALL',
        }
    }
    env.update(
        {
            'PYTHONPATH': str(repo_root),
            'FRONTEND_DIRECTORY': str(frontend_build),
            'OPENHANDS_SUPPRESS_BANNER': '1',
            'OPENHANDS_CONFIG_CLS': 'server.config.SaaSServerConfig',
            'ENABLE_KEYCLOAK': keycloak,
            'NATIVE_AUTH_APP_ORIGIN': 'https://app.example.com',
            'GITHUB_APP_CLIENT_ID': client_id,
            'GITHUB_APP_CLIENT_SECRET': 'test-oauth-secret' if client_id else '',
            'NATIVE_GIT_GITHUB_OAUTH_ENABLED': 'true' if client_id else 'false',
            'GITHUB_APP_ID': app_id,
            'GITHUB_APP_PRIVATE_KEY': private_key,
            'POSTHOG_CLIENT_KEY': 'test-posthog-key',
            'EXPECTED_WEBHOOKS': str(webhooks),
        }
    )
    script = textwrap.dedent(
        """
        import os
        from unittest.mock import patch

        with (
            patch('dotenv.load_dotenv', return_value=False),
            patch('server.config.SaaSServerConfig._get_app_slug'),
            patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected network request')),
        ):
            import saas_server

        paths = {getattr(route, 'path', None) for route in saas_server.app.routes}
        assert ('/integration/github/events' in paths) == (os.environ['EXPECTED_WEBHOOKS'] == 'True')
        if os.environ['ENABLE_KEYCLOAK'] == 'false':
            assert '/api/git-connections/{provider}/oauth' in paths
            assert '/oauth/git/{provider}/callback' in paths
        """
    )
    result = subprocess.run(
        [sys.executable, '-c', script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_oauth_callback_route_precedes_spa_mount(tmp_path: Path) -> None:
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
