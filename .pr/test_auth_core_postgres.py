"""Run the preserved PostgreSQL regressions against this disposable container."""

import json
import os
from pathlib import Path

import pytest

state = json.loads(Path('.pr/auth-core-postgres-state.json').read_text())
os.environ['OH_AUTH_TEST_POSTGRES_PORT'] = str(state['port'])
raise SystemExit(
    pytest.main(['tests/unit/server/auth/test_local_auth_postgres.py', '-q'])
)
