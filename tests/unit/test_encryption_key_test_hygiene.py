"""Guard that every test process uses the same encryption key.

Some tests encrypt a value in the pytest process and decrypt it in another
process: the migration tests run ``alembic`` in a subprocess (see
``tests.postgres_testdb.run_alembic``). Both processes must derive the same
key. If the key came from a ``.keys`` file that each process reads or creates
on its own, each pytest-xdist worker could create a different key, and such a
test would fail only on the workers that lost that race.

The root ``conftest.py`` sets the key through the environment, which every
subprocess inherits. This guard fails if that stops being true.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from storage.encrypt_utils import encrypt_value
from tests.postgres_testdb import REPO_ROOT

_DECRYPT = (
    'import sys\n'
    'from storage.encrypt_utils import decrypt_value\n'
    'print(decrypt_value(sys.argv[1]), end="")\n'
)


def test_a_subprocess_without_key_files_decrypts_what_pytest_encrypted(
    tmp_path: Path,
):
    # An empty persistence directory: the subprocess cannot read a key file
    # that this process wrote, so the key must come from the environment.
    env = {key: value for key, value in os.environ.items() if key != 'FILE_STORE_PATH'}
    env['OH_PERSISTENCE_DIR'] = str(tmp_path)
    ciphertext = encrypt_value('shared-secret')

    result = subprocess.run(
        [sys.executable, '-c', _DECRYPT, ciphertext],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == 'shared-secret'
