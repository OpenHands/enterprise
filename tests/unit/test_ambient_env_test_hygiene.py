"""Guard that the tests run on a developer's machine the way they run in CI.

The root ``conftest.py`` calls ``tests.ambient_env.isolate_from_ambient_environment``
before any test module is imported; that module explains why. These guards
fail if that stops happening, or stops being enough.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import dotenv
import pytest

from tests.ambient_env import MIGRATION_INPUTS

REPO_ROOT = Path(__file__).resolve().parents[2]

_ENV_READ = re.compile(
    r"""os\.(?:getenv\(|environ\.get\(|environ\[)\s*['"]([A-Z0-9_]+)['"]"""
)
_SET_BY_RUN_ALEMBIC = ('DB_', 'GCP_', 'PG')


def test_every_variable_a_migration_reads_is_cleared():
    read = {
        name
        for path in (REPO_ROOT / 'migrations').rglob('*.py')
        for name in _ENV_READ.findall(path.read_text())
    }
    assert 'KEYCLOAK_CLIENT_ID' in read  # the pattern still matches real reads

    unlisted = sorted(
        name
        for name in read - set(MIGRATION_INPUTS)
        if not name.startswith(_SET_BY_RUN_ALEMBIC)
    )

    assert unlisted == [], (
        'Migrations read these variables; add them to '
        'tests.ambient_env.MIGRATION_INPUTS so a developer who has them set '
        f'gets the same migrations as CI: {unlisted}'
    )


def test_an_ambient_session_api_key_does_not_turn_on_session_auth():
    # A fresh interpreter, because this one imported the dependencies module
    # long ago. Importing the root conftest is what pytest does first.
    probe = (
        'import conftest\n'
        'from openhands.app_server.utils import dependencies\n'
        'print(repr(dependencies._SESSION_API_KEY))\n'
    )
    result = subprocess.run(
        [sys.executable, '-c', probe],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            'SESSION_API_KEY': 'ambient-key',
            'OPENHANDS_SUPPRESS_BANNER': '1',
        },
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.splitlines()[-1] == 'None'


def test_dotenv_files_are_not_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_file = tmp_path / '.env'
    env_file.write_text('AMBIENT_ENV_HYGIENE_PROBE=loaded\n')
    monkeypatch.delenv('AMBIENT_ENV_HYGIENE_PROBE', raising=False)

    dotenv.load_dotenv(env_file)

    assert 'AMBIENT_ENV_HYGIENE_PROBE' not in os.environ
