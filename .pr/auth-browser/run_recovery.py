"""Invoke the single interactive operator-recovery entrypoint on the QA DB."""

import os
import runpy
from pathlib import Path

work = Path(__file__).parent
runner = runpy.run_path(str(work / 'run_app.py'))
_, env = runner['environment']()
assert 'SMTP_HOST' not in env
assert not any(name.startswith('KEYCLOAK_') for name in env)
root = runner['ROOT']
os.chdir(root)
os.execve(
    str(root / '.venv/bin/python'),
    [
        str(root / '.venv/bin/python'),
        '-m',
        'server.auth.local.recover_admin',
    ],
    env,
)
