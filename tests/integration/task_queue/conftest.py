"""Docker Compose fixtures for the task queue POC. Run with --confcutdir as documented.

The candidate under test is ``candidate/`` (added by each candidate branch), or
the directory named by ``TQ_CANDIDATE``, relative to this one. Without either,
the harness's own negative control, ``harness/naive``, runs.
"""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).parent
CANDIDATE = HERE / os.environ.get(
    'TQ_CANDIDATE', 'candidate' if (HERE / 'candidate').is_dir() else 'harness/naive'
)
MANIFEST = json.loads((CANDIDATE / 'manifest.json').read_text())
RESULTS = HERE / 'results'
# Results file name; the negative control records its guarded run separately.
RESULTS_NAME = os.environ.get('TQ_RESULTS_NAME', MANIFEST['name'])
FAKETIME = '/usr/local/lib/libfaketime.so.1'


class Stack:
    def __init__(self, env: dict[str, str]):
        self.project = f'tq-{MANIFEST["name"]}-{uuid.uuid4().hex[:6]}'
        self.env = {**os.environ, **env}

    def compose(self, *args: str, check: bool = True) -> str:
        cmd = [
            'docker',
            'compose',
            '-p',
            self.project,
            '-f',
            str(HERE / 'compose.yaml'),
        ]
        cmd += ['-f', str(CANDIDATE / 'compose.yaml'), *args]
        done = subprocess.run(
            cmd, env=self.env, cwd=HERE, capture_output=True, text=True, check=False
        )
        if check and done.returncode:
            raise RuntimeError(
                f'{" ".join(args)} failed:\n{done.stdout}\n{done.stderr}'
            )
        return done.stdout

    def sql(self, query: str) -> list[list[str]]:
        out = self.compose(
            'exec',
            '-T',
            'postgres',
            'psql',
            '-U',
            'poc',
            '-d',
            'poc',
            '-At',
            '-F',
            '\t',
            '-c',
            query,
        )
        return [line.split('\t') for line in out.splitlines() if line]

    def wait_for(self, query: str, timeout: float, poll: float = 1) -> list[list[str]]:
        """Poll until the query returns a row."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if rows := self.sql(query):
                return rows
            time.sleep(poll)
        raise TimeoutError(f'no rows after {timeout}s: {query}')

    def services(self) -> list[str]:
        return self.compose('config', '--services').split()


@pytest.fixture(scope='session', autouse=True)
def base_image():
    subprocess.run(
        ['docker', 'build', '-q', '-t', 'tq-poc-base', str(HERE / 'harness')],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def stack(request):
    """Factory: stack(POC_INTERVAL_SECONDS='10', ...) starts the candidate."""
    started: list[Stack] = []

    def start(**env: str) -> Stack:
        s = Stack(env)
        started.append(s)
        s.compose('up', '-d', '--build')
        return s

    yield start
    for s in started:
        logs = RESULTS / 'logs' / f'{RESULTS_NAME}-{request.node.name}.log'
        logs.parent.mkdir(parents=True, exist_ok=True)
        logs.write_text(s.compose('logs', '--no-color', '-t', check=False))
        s.compose('down', '-v', '--remove-orphans', check=False)


@pytest.fixture
def record():
    """record(check, data) merges one check's evidence into results/<name>.json."""

    def write(check: str, data: dict) -> None:
        path = RESULTS / f'{RESULTS_NAME}.json'
        results = json.loads(path.read_text()) if path.exists() else {}
        results[check] = {
            **data,
            'recorded_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }
        path.write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')

    return write
