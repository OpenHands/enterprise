"""Remove only the disposable resources whose ownership is recorded below."""

import json
import os
import re
import shutil
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

WORK = Path(__file__).resolve().parent
ROOT = WORK.parents[1]
state = json.loads((WORK / 'state.json').read_text())
pg_path = ROOT / '.pr/auth-core-postgres-state.json'
pg = json.loads(pg_path.read_text())
assert pg['name'] == 'oh-auth-core-pg-ca5a3a34ca4c'
assert state['kc_name'].startswith('oh-auth-keycloak-')
assert state['redis_name'].startswith('oh-auth-keycloak-redis-')
assert state['database'].startswith('auth_keycloak_')

for port, expected in [
    (state['app_port'], 'uvicorn saas_server:app'),
    (state['proxy_port'], '.pr/auth-keycloak/fault_proxy.py'),
]:
    lookup = subprocess.run(
        ['lsof', '-tiTCP:' + str(port), '-sTCP:LISTEN'], text=True, capture_output=True
    )
    for raw_pid in lookup.stdout.split():
        pid = int(raw_pid)
        command = subprocess.check_output(
            ['ps', '-p', str(pid), '-o', 'command='], text=True
        )
        assert expected in command, f'Unexpected process on task port {port}'
        os.kill(pid, signal.SIGTERM)

for container in (state['kc_name'], state['redis_name'], pg['name']):
    subprocess.run(
        ['docker', 'rm', '-f', container], check=True, stdout=subprocess.DEVNULL
    )

time.sleep(1)
for port in (state['app_port'], state['proxy_port']):
    result = subprocess.run(
        ['lsof', '-tiTCP:' + str(port), '-sTCP:LISTEN'], capture_output=True
    )
    assert not result.stdout, f'Task listener remains on {port}'

secrets = [
    state[key]
    for key in (
        'password',
        'admin_password',
        'client_secret',
        'broker_secret',
        'jwt_secret',
        'api_key',
        'legacy_llm_secret',
    )
]
for path in WORK.rglob('*'):
    if not path.is_file() or path.suffix not in ('.log', '.jsonl', '.yml'):
        continue
    content = path.read_text()
    for secret in secrets:
        content = content.replace(secret, '[synthetic-secret-redacted]')
    content = re.sub(
        r'([?&](?:code|session_state)=)[^&\s\\"<>]+', r'\1[redacted]', content
    )
    path.write_text(content)

for path in [
    *WORK.glob('*state.json'),
    WORK / 'keycloak.env',
    WORK / 'localhost.key',
    WORK / 'localhost.crt',
]:
    path.unlink(missing_ok=True)
for directory in [
    WORK / 'realms',
    WORK / 'persistence',
    WORK / 'browser',
    ROOT / '.pr/account-mypy-cache',
    WORK / '__pycache__',
]:
    if directory.exists():
        shutil.rmtree(directory)

cleanup = {
    'completed_at': datetime.now(UTC).isoformat(),
    'containers_removed': [state['kc_name'], state['redis_name'], pg['name']],
    'database_removed_with_disposable_postgres': state['database'],
    'listeners_stopped': [state['app_port'], state['proxy_port']],
    'browser_session_closed': 'auth-keycloak-qa',
    'synthetic_secrets_browser_state_and_private_keys_removed': True,
    'owner_mypy_cache_removed': True,
}
(WORK / 'cleanup.json').write_text(json.dumps(cleanup, indent=2))
pg['removed'] = True
pg['removed_at'] = cleanup['completed_at']
pg_path.write_text(json.dumps(pg, indent=2))
print(json.dumps(cleanup, indent=2))
