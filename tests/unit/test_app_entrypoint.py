"""The app container's entrypoint must hand its process over to the command.

Docker sends the stop signal to PID 1. A shell that runs the command as a child
never forwards it, so the app is killed after the stop timeout instead of
shutting down.

The app is then PID 1 and does not reap orphaned children, so Compose runs it
with an init process.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / 'containers/app/entrypoint.sh'
COMPOSE_FILE = REPO_ROOT / 'docker-compose.yml'

REPORT_PID_AND_WAIT_FOR_SIGTERM = (
    'import os, signal, sys, time;'
    'signal.signal(signal.SIGTERM, lambda *a: sys.exit(0));'
    'print(os.getpid(), flush=True);'
    'time.sleep(30)'
)


@pytest.fixture
def fake_root(tmp_path):
    """Put an `id` first on PATH that reports root, so the root path runs here."""
    fake_id = tmp_path / 'id'
    fake_id.write_text('#!/bin/sh\necho 0\n')
    fake_id.chmod(0o755)
    return {'PATH': f'{tmp_path}:{os.environ["PATH"]}'}


def _read_command_pid(process: subprocess.Popen) -> int:
    for line in process.stdout:
        if line.strip().isdigit():
            return int(line)
    raise AssertionError('the command never started')


@pytest.mark.parametrize(
    'env',
    [{'NO_SETUP': 'true'}, {'SANDBOX_USER_ID': '0'}],
    ids=['no-setup', 'root'],
)
def test_the_command_replaces_the_entrypoint_and_gets_the_stop_signal(env, fake_root):
    process = subprocess.Popen(
        [
            'bash',
            str(ENTRYPOINT),
            sys.executable,
            '-c',
            REPORT_PID_AND_WAIT_FOR_SIGTERM,
        ],
        env={**os.environ, **fake_root, **env},
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert _read_command_pid(process) == process.pid
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_compose_runs_the_app_with_an_init_process():
    compose = yaml.safe_load(COMPOSE_FILE.read_text())

    assert compose['services']['app']['init'] is True
