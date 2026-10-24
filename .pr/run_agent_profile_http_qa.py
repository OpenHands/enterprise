"""Run the profile HTTP harness with an isolated SDK server and temporary state."""

import os
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
artifacts = root / '.pr' / 'logs'
artifacts.mkdir(exist_ok=True)
python = root / '.venv/bin/python'
os.umask(0o077)
with tempfile.TemporaryDirectory(prefix='pr763-http-qa-') as state:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ)
    env.update(
        SESSION_API_KEY=secrets.token_urlsafe(32),
        OH_CONVERSATIONS_PATH=f'{state}/conversations',
        OH_WORKSPACE_PATH=f'{state}/workspace',
        OH_BASH_EVENTS_DIR=f'{state}/bash-events',
        OH_CONVERSATION_WORKTREE_ROOT=f'{state}/worktrees',
        OH_ENABLE_VSCODE='false',
        OH_TELEMETRY_CONSENT='false',
        OPENHANDS_AGENT_SERVER_CONFIG_PATH=f'{state}/config.json',
        OPENHANDS_SUPPRESS_BANNER='1',
        QA_WORKSPACE=f'{state}/workspace',
        QA_REPORT=os.environ.get(
            'QA_REPORT', str(root / '.pr' / 'http-profile-refresh-results.json')
        ),
        AGENT_SERVER_URL=f'http://127.0.0.1:{port}',
        PYTHONPATH=str(root),
        TMPDIR='/tmp',
    )
    Path(env['QA_WORKSPACE']).mkdir()
    with (artifacts / 'agent-server.log').open('w') as log:
        server = subprocess.Popen(
            [
                str(python),
                '-m',
                'openhands.agent_server',
                '--host',
                '127.0.0.1',
                '--port',
                str(port),
            ],
            cwd=root,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(120):
                if server.poll() is not None:
                    raise RuntimeError('Agent server exited; inspect private log')
                try:
                    req = urllib.request.Request(
                        env['AGENT_SERVER_URL'] + '/server_info',
                        headers={'X-Session-API-Key': env['SESSION_API_KEY']},
                    )
                    with urllib.request.urlopen(req, timeout=2) as res:
                        if res.status == 200:
                            break
                except Exception:
                    time.sleep(0.5)
            else:
                raise RuntimeError('Agent server startup timed out')
            result = subprocess.run(
                [str(python), '.pr/agent_profile_http_qa.py'],
                cwd=root,
                env=env,
                timeout=300,
            )
            if result.returncode:
                raise SystemExit(result.returncode)
        finally:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
    print('Owned agent server stopped; isolated runtime state removed.')
