"""Guard that every test process uses the same encryption key.

Some tests encrypt a value in the pytest process and decrypt it in another
process: the migration tests run ``alembic`` in a subprocess (see
``tests.postgres_testdb.run_alembic``). Both processes must derive the same
key. If the key came from a ``.keys`` file that each process reads or creates
on its own, each pytest-xdist worker could create a different key, and such a
test would fail only on the workers that lost that race.

The root ``conftest.py`` sets the key through the environment, which every
new process inherits. This guard fails if that stops being true.
"""

from __future__ import annotations

import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from storage.encrypt_utils import decrypt_value, encrypt_value


def test_a_fresh_process_without_key_files_decrypts_what_pytest_encrypted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Encrypt first: the key this process uses must not come from tmp_path.
    ciphertext = encrypt_value('shared-secret')
    # An empty persistence directory for the new process: it cannot read a key
    # file that this process wrote, so the key must come from the environment.
    monkeypatch.setenv('OH_PERSISTENCE_DIR', str(tmp_path))

    # ``spawn`` starts a fresh interpreter, like the alembic subprocess does.
    spawn = multiprocessing.get_context('spawn')
    with ProcessPoolExecutor(max_workers=1, mp_context=spawn) as pool:
        assert pool.submit(decrypt_value, ciphertext).result() == 'shared-secret'
