"""P5: the candidate on Kubernetes (kind), 2 replicas in a Deployment.

Kubernetes replaces a pod under a new name, and rolls a Deployment by starting
new pods before sending SIGTERM to old ones. P5 repeats P1, P3 (force-deleted
pod) and P7 (rollout restart) under those semantics. Needs a running kind
cluster; skipped otherwise. Select it with ``-k test_p5``; it is not part of the
Compose run.
"""

import json
import os
import subprocess
import time
import uuid

import pytest

from conftest import CANDIDATE, HERE, MANIFEST, RESULTS

NODE = os.environ.get('TQ_KIND_NODE', 'openhands-local-kind-control-plane')
POSTGRES = (
    'postgres@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297'
)
IMAGE = f'tq-poc-{MANIFEST["name"]}:p5'
K8S = MANIFEST.get('k8s', {})
# Variant env, e.g. TQ_K8S_ENV='POC_DBOS_EXECUTOR=hostname'.
EXTRA_ENV = dict(
    kv.split('=', 1) for kv in os.environ.get('TQ_K8S_ENV', '').split(',') if kv
)


def run(*cmd: str, input: str | None = None, check: bool = True) -> str:
    done = subprocess.run(cmd, input=input, capture_output=True, text=True, check=False)
    if check and done.returncode:
        raise RuntimeError(f'{" ".join(cmd)} failed:\n{done.stdout}\n{done.stderr}')
    return done.stdout


def load(image: str) -> None:
    # `kind load` cannot read newer node containerd configs; import directly.
    save = subprocess.Popen(['docker', 'save', image], stdout=subprocess.PIPE)
    subprocess.run(
        [
            'docker',
            'exec',
            '-i',
            NODE,
            'ctr',
            '-n',
            'k8s.io',
            'images',
            'import',
            '--all-platforms',
            '-',
        ],
        stdin=save.stdout,
        check=True,
        capture_output=True,
    )
    save.wait()


@pytest.fixture(scope='module', autouse=True)
def images():
    if not run('docker', 'ps', '-q', '-f', f'name={NODE}', check=False).strip():
        pytest.skip(f'kind node {NODE} is not running')
    run('docker', 'build', '-q', '-t', IMAGE, str(CANDIDATE))
    load(IMAGE)  # Postgres is pulled by the node itself.


def env_list(env: dict[str, str]) -> list[dict]:
    return [{'name': k, 'value': v} for k, v in env.items()]


def manifests(
    ns: str, interval: int, job_seconds: float, grace: int
) -> tuple[str, str]:
    initdb = (HERE / 'harness' / 'initdb.sql').read_text()
    app_env = {
        'POC_DATABASE_URL': 'postgresql://poc_app:poc_app@postgres/poc',
        'POC_INTERVAL_SECONDS': str(interval),
        'POC_JOB_SECONDS': str(job_seconds),
        **K8S.get('env', {}),
        **EXTRA_ENV,
    }
    docs = [
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns}},
        {
            'apiVersion': 'v1',
            'kind': 'ConfigMap',
            'metadata': {'name': 'initdb', 'namespace': ns},
            'data': {'00-harness.sql': initdb},
        },
        {
            'apiVersion': 'apps/v1',
            'kind': 'Deployment',
            'metadata': {'name': 'postgres', 'namespace': ns},
            'spec': {
                'selector': {'matchLabels': {'app': 'postgres'}},
                'template': {
                    'metadata': {'labels': {'app': 'postgres'}},
                    'spec': {
                        'containers': [
                            {
                                'name': 'postgres',
                                'image': POSTGRES,
                                'imagePullPolicy': 'IfNotPresent',
                                'env': env_list(
                                    {
                                        'POSTGRES_USER': 'poc',
                                        'POSTGRES_PASSWORD': 'poc',
                                        'POSTGRES_DB': 'poc',
                                    }
                                ),
                                'volumeMounts': [
                                    {
                                        'name': 'initdb',
                                        'mountPath': '/docker-entrypoint-initdb.d',
                                    }
                                ],
                                'readinessProbe': {
                                    'exec': {
                                        'command': [
                                            'pg_isready',
                                            '-h',
                                            '127.0.0.1',
                                            '-U',
                                            'poc',
                                            '-d',
                                            'poc',
                                        ]
                                    },
                                    'periodSeconds': 1,
                                },
                            }
                        ],
                        'volumes': [
                            {'name': 'initdb', 'configMap': {'name': 'initdb'}}
                        ],
                    },
                },
            },
        },
        {
            'apiVersion': 'v1',
            'kind': 'Service',
            'metadata': {'name': 'postgres', 'namespace': ns},
            'spec': {'selector': {'app': 'postgres'}, 'ports': [{'port': 5432}]},
        },
        {
            'apiVersion': 'batch/v1',
            'kind': 'Job',
            'metadata': {'name': 'migrate', 'namespace': ns},
            'spec': {
                'backoffLimit': 10,
                'template': {
                    'spec': {
                        'restartPolicy': 'OnFailure',
                        'containers': [
                            {
                                'name': 'migrate',
                                'image': IMAGE,
                                'imagePullPolicy': 'Never',
                                'command': [
                                    'alembic',
                                    '-c',
                                    '/harness/alembic.ini',
                                    'upgrade',
                                    'head',
                                ],
                                'env': env_list(
                                    {
                                        'POC_MIGRATE_URL': 'postgresql://poc:poc@postgres/poc'
                                    }
                                ),
                            }
                        ],
                    }
                },
            },
        },
    ]
    app = {
        'apiVersion': 'apps/v1',
        'kind': 'Deployment',
        'metadata': {'name': 'app', 'namespace': ns},
        'spec': {
            'replicas': 2,
            'selector': {'matchLabels': {'app': 'app'}},
            'strategy': {
                'type': 'RollingUpdate',
                'rollingUpdate': {'maxSurge': 1, 'maxUnavailable': 0},
            },
            'template': {
                'metadata': {'labels': {'app': 'app'}},
                'spec': {
                    'terminationGracePeriodSeconds': grace,
                    'containers': [
                        {
                            'name': 'app',
                            'image': IMAGE,
                            'imagePullPolicy': 'Never',
                            'command': K8S['command'],
                            'env': env_list(app_env),
                        }
                    ],
                },
            },
        },
    }
    return '\n---\n'.join(json.dumps(d) for d in docs), json.dumps(app)


class Cluster:
    def __init__(self, interval: int, job_seconds: float, grace: int = 30):
        self.ns = f'tq-p5-{MANIFEST["name"]}-{uuid.uuid4().hex[:5]}'
        base, app = manifests(self.ns, interval, job_seconds, grace)
        run('kubectl', 'apply', '-f', '-', input=base)
        run(
            'kubectl',
            '-n',
            self.ns,
            'wait',
            '--for=condition=complete',
            'job/migrate',
            '--timeout=300s',
        )
        run('kubectl', 'apply', '-f', '-', input=app)
        run(
            'kubectl',
            '-n',
            self.ns,
            'rollout',
            'status',
            'deploy/app',
            '--timeout=300s',
        )

    def kubectl(self, *args: str, check: bool = True) -> str:
        return run('kubectl', '-n', self.ns, *args, check=check)

    def sql(self, query: str) -> list[list[str]]:
        out = self.kubectl(
            'exec',
            'deploy/postgres',
            '--',
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
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if rows := self.sql(query):
                return rows
            time.sleep(poll)
        raise TimeoutError(f'no rows after {timeout}s: {query}')

    def pods(self) -> list[str]:
        return (
            self.kubectl('get', 'pods', '-l', 'app=app', '-o', 'name')
            .replace('pod/', '')
            .split()
        )


@pytest.fixture
def cluster(request):
    made: list[Cluster] = []

    def start(**kw) -> Cluster:
        c = Cluster(**kw)
        made.append(c)
        return c

    yield start
    name = os.environ.get('TQ_RESULTS_NAME', MANIFEST['name'])
    for c in made:
        logs = RESULTS / 'logs' / f'{name}-{request.node.name}.log'
        logs.parent.mkdir(parents=True, exist_ok=True)
        logs.write_text(
            c.kubectl(
                'logs',
                '-l',
                'app=app',
                '--prefix',
                '--tail=-1',
                '--timestamps',
                check=False,
            )
        )
        run('kubectl', 'delete', 'namespace', c.ns, '--wait=false', check=False)


def outcome(c: Cluster, slot: str, run_id: str, victim: str) -> tuple[str, list]:
    rows = c.sql(
        f"SELECT id, replica, finished_by FROM poc_job_runs WHERE slot = '{slot}' ORDER BY id"
    )
    finished = [r for r in rows if r[2]]
    if not finished:
        return 'lost', rows
    if finished[0][0] == run_id and finished[0][2] == victim:
        return 'drained', rows
    if [r[0] for r in finished] == [run_id] and len(rows) == 1:
        return 'resumed', rows
    return 'retried', rows


def in_flight(c: Cluster, timeout: float) -> tuple[str, str, str]:
    return tuple(
        c.wait_for(
            'SELECT id, slot, replica FROM poc_job_runs '
            "WHERE finished_at IS NULL AND started_at < now() - interval '5 seconds' LIMIT 1",
            timeout=timeout,
        )[0]
    )


def settle(c: Cluster, slot: str, timeout: float) -> bool:
    """Wait for the occurrence to finish, then for a later one: True if the schedule continued."""
    try:
        c.wait_for(
            f"SELECT 1 FROM poc_job_runs WHERE slot = '{slot}' AND finished_at IS NOT NULL",
            timeout=timeout,
        )
    except TimeoutError:
        pass
    try:
        c.wait_for(
            f"SELECT 1 FROM poc_job_runs WHERE slot > '{slot}' AND finished_at IS NOT NULL",
            timeout=timeout,
        )
        return True
    except TimeoutError:
        return False


def write(check: str, data: dict) -> None:
    name = os.environ.get('TQ_RESULTS_NAME', MANIFEST['name'])
    path = RESULTS / f'{name}.json'
    results = json.loads(path.read_text()) if path.exists() else {}
    results[check] = {
        **data,
        'extra_env': EXTRA_ENV,
        'recorded_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    path.write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')


def test_p5_once_per_occurrence(cluster):
    c = cluster(interval=10, job_seconds=1)
    c.wait_for(
        'SELECT 1 FROM poc_job_runs HAVING count(DISTINCT slot) >= 12', timeout=210
    )
    rows = c.sql(
        'SELECT extract(epoch FROM slot)::bigint, count(*) FROM poc_job_runs GROUP BY slot ORDER BY slot'
    )
    slots = [int(r[0]) for r in rows]
    doubles = [r for r in rows if int(r[1]) > 1]
    missing = sorted(set(range(slots[1], slots[-1], 10)) - set(slots))
    write('P5-P1', {'occurrences': len(rows), 'doubles': doubles, 'missing': missing})
    assert not doubles and not missing


def test_p5_pod_force_deleted(cluster):
    c = cluster(interval=60, job_seconds=30)
    run_id, slot, victim = in_flight(c, timeout=120)
    killed = time.monotonic()
    c.kubectl('delete', 'pod', victim, '--grace-period=0', '--force')
    next_ran = settle(c, slot, timeout=180)
    result, rows = outcome(c, slot, run_id, victim)
    finished = [r for r in rows if r[2]]
    write(
        'P5-P3-force-deleted',
        {
            'victim': victim,
            'outcome': result,
            'runs_for_occurrence': rows,
            'seconds_observed': round(time.monotonic() - killed),
            'next_occurrence_ran': next_ran,
            'pods_after': c.pods(),
        },
    )
    assert len(finished) <= 1 and next_ran


@pytest.mark.parametrize('grace', [10, 60], ids=['grace-10s', 'grace-60s'])
def test_p5_rollout_restart(cluster, grace):
    c = cluster(interval=60, job_seconds=30, grace=grace)
    run_id, slot, victim = in_flight(c, timeout=120)
    started = time.monotonic()
    c.kubectl('rollout', 'restart', 'deploy/app')
    c.kubectl('rollout', 'status', 'deploy/app', f'--timeout={grace * 2 + 180}s')
    rollout_seconds = round(time.monotonic() - started, 1)
    next_ran = settle(c, slot, timeout=180)
    result, rows = outcome(c, slot, run_id, victim)
    finished = [r for r in rows if r[2]]
    doubles = c.sql(
        'SELECT slot, count(*) FROM poc_job_runs WHERE finished_at IS NOT NULL '
        'GROUP BY slot HAVING count(*) > 1'
    )
    write(
        f'P5-P7-rollout-grace-{grace}s',
        {
            'victim': victim,
            'grace_seconds': grace,
            'outcome': result,
            'runs_for_occurrence': rows,
            'rollout_seconds': rollout_seconds,
            'doubles_any_occurrence': doubles,
            'next_occurrence_ran': next_ran,
            'pods_after': c.pods(),
        },
    )
    assert len(finished) <= 1 and not doubles and next_ran
