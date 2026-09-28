"""P6: workloads beyond the app, and idle memory and CPU per service."""

import json
import subprocess

from conftest import MANIFEST

INFRA = {'postgres', 'migrate'}


def test_p6_footprint(stack, record):
    s = stack(POC_INTERVAL_SECONDS='10', POC_JOB_SECONDS='1')
    s.wait_for(
        'SELECT 1 FROM poc_job_runs HAVING count(DISTINCT slot) >= 3', timeout=120
    )
    ids = s.compose('ps', '-q').split()
    stats = subprocess.run(
        ['docker', 'stats', '--no-stream', '--format', '{{json .}}', *ids],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    usage = {}
    for line in stats:
        row = json.loads(line)
        service = row['Name'].removeprefix(f'{s.project}-').rsplit('-', 1)[0]
        usage[service] = {
            'memory': row['MemUsage'].split(' / ')[0],
            'cpu': row['CPUPerc'],
        }
    services = set(s.services())
    record(
        'P6',
        {
            'extra_workloads': sorted(services - INFRA - set(MANIFEST['app_services'])),
            'app_services': MANIFEST['app_services'],
            'usage': usage,
        },
    )
