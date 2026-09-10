"""Run the real SaaS app using only disposable test resources and synthetic data."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / '.pr/auth-browser'


def environment():
    state = json.loads((WORK / 'state.json').read_text())
    env = {
        key: os.environ[key]
        for key in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT')
        if key in os.environ
    }
    env.update(
        {
            'PYTHON_DOTENV_DISABLED': '1',
            'PYTHONPATH': str(ROOT),
            'PYTHONUNBUFFERED': '1',
            'DB_HOST': '127.0.0.1',
            'DB_PORT': str(state['postgres_port']),
            'DB_USER': 'postgres',
            'DB_PASS': 'postgres',
            'DB_NAME': state['database'],
            'DB_DRIVER': 'pg8000',
            'DB_SSL_MODE': 'disable',
            'REDIS_HOST': '127.0.0.1',
            'REDIS_PORT': str(state['redis_port']),
            'REDIS_PASSWORD': '',
            'OPENHANDS_CONFIG_CLS': 'server.config.SaaSServerConfig',
            'OH_APP_MODE': 'saas',
            'OH_DEPLOYMENT_MODE': 'self_hosted',
            'OH_PERSISTENCE_DIR': str(WORK / 'persistence'),
            'WEB_HOST': f'https://localhost:{state["app_port"]}',
            'OH_WEB_URL': f'https://localhost:{state["app_port"]}',
            'PERMITTED_CORS_ORIGINS': f'https://localhost:{state["app_port"]}',
            'OH_BOOTSTRAP_ADMIN_EMAIL': 'admin@auth-test.example',
            'OH_BOOTSTRAP_ADMIN_PASSWORD': 'Synthetic bootstrap password 2026',
            'OH_ENABLE_ONBOARDING': 'true',
            'RUNTIME': 'process',
            'OH_SANDBOX_KIND': 'ProcessSandboxServiceInjector',
            'OH_SANDBOX_BASE_WORKING_DIR': str(WORK / 'sandboxes'),
            'OPENHANDS_LLM_PROVIDER_ROUTE': 'direct',
            'OPENHANDS_DEFAULT_LLM_MODEL': 'openai/gpt-4o-mini',
            'OPENHANDS_DEFAULT_LLM_BASE_URL': f'http://127.0.0.1:{state["llm_port"]}/v1',
            'OPENHANDS_DEFAULT_LLM_API_KEY': 'synthetic-local-model-key',
            'LITELLM_LOCAL_MODEL_COST_MAP': 'True',
            'POSTHOG_CLIENT_KEY': '',
            'POSTHOG_HOST': f'http://127.0.0.1:{state["llm_port"]}',
            'LOG_ALL_EVENTS': 'false',
            'SSL_CERT_FILE': str(WORK / 'localhost.crt'),
            'REQUESTS_CA_BUNDLE': str(WORK / 'localhost.crt'),
        }
    )
    if '--smtp' in sys.argv:
        env.update(
            {
                'SMTP_HOST': '127.0.0.1',
                'SMTP_PORT': str(state['smtp_port']),
                'SMTP_USE_TLS': 'false',
                'SMTP_FROM_EMAIL': 'openhands@auth-test.example',
            }
        )
    if '--provider-boundary' in sys.argv:
        env['PYTHONPATH'] = str(WORK / 'boundary') + os.pathsep + str(ROOT)
        env['AUTH_QA_EXTERNAL_PORT'] = str(state['llm_port'])
    if '--runtime-transport' in sys.argv:
        env['OH_SANDBOX_PYTHON_EXECUTABLE'] = str(WORK / 'agent_transport.py')
        env['CONTENT_SECURITY_POLICY'] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline' https://us-assets.i.posthog.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob: https:; "
            "connect-src 'self' ws: wss: https://localhost:8000 https://us.i.posthog.com https://us-assets.i.posthog.com; "
            "frame-src 'self' https://localhost:8000; frame-ancestors 'self'; object-src 'none'; "
            "base-uri 'self'; form-action 'self'; worker-src 'self' blob:"
        )
    return state, env


if __name__ == '__main__':
    state, env = environment()
    if '--migrate' in sys.argv:
        raise SystemExit(
            subprocess.call(
                [str(ROOT / '.venv/bin/alembic'), 'upgrade', 'head'], cwd=ROOT, env=env
            )
        )
    os.chdir(ROOT)
    os.execve(
        str(ROOT / '.venv/bin/python'),
        [
            str(ROOT / '.venv/bin/python'),
            '-m',
            'uvicorn',
            'saas_server:app',
            '--host',
            '127.0.0.1',
            '--port',
            str(state['app_port']),
            '--ssl-keyfile',
            str(WORK / 'localhost.key'),
            '--ssl-certfile',
            str(WORK / 'localhost.crt'),
        ],
        env,
    )
