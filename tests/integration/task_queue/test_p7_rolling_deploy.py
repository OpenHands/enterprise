"""P7: a replica is stopped mid-job the way a rolling deploy stops it.

SIGTERM, then SIGKILL after the grace period. P3 kills without warning; this
check measures whether the candidate's graceful shutdown finishes the job,
hands it back, or drops it.
"""

import json
import time

import pytest

from conftest import MANIFEST

INTERVAL = 60
JOB_SECONDS = 30


@pytest.mark.parametrize('grace', [10, 60], ids=['grace-10s', 'grace-60s'])
def test_p7_rolling_deploy(stack, record, grace):
    s = stack(POC_INTERVAL_SECONDS=str(INTERVAL), POC_JOB_SECONDS=str(JOB_SECONDS))
    run_id, slot, victim = s.wait_for(
        'SELECT id, slot, replica FROM poc_job_runs '
        "WHERE finished_at IS NULL AND started_at < now() - interval '5 seconds' LIMIT 1",
        timeout=INTERVAL + 60,
    )[0]
    assert victim in MANIFEST['job_services'], f'{victim} is not a declared job service'
    started = time.monotonic()
    s.compose('stop', '-t', str(grace), victim)
    stop_seconds = round(time.monotonic() - started, 1)
    state = json.loads(
        s.compose('ps', '-a', '--format', 'json', victim).splitlines()[0]
    )
    # 137 = killed at the deadline; anything else means it exited on its own.
    exit_code = state['ExitCode']
    sole = MANIFEST['job_services'] == [victim]
    if sole:
        # The only job runner: a one-replica Deployment would start a new pod.
        s.compose('start', victim)

    try:
        s.wait_for(
            f"SELECT 1 FROM poc_job_runs WHERE slot = '{slot}' AND finished_at IS NOT NULL",
            timeout=INTERVAL * 2 + JOB_SECONDS,
        )
    except TimeoutError:
        pass
    rows = s.sql(
        f"SELECT id, replica, finished_by FROM poc_job_runs WHERE slot = '{slot}' ORDER BY id"
    )
    finished = [r for r in rows if r[2]]
    if not finished:
        outcome = 'lost'
    elif finished[0][0] == run_id and finished[0][2] == victim:
        outcome = 'drained'  # the stopping replica finished its own run before exiting
    elif [r[0] for r in finished] == [run_id] and len(rows) == 1:
        outcome = 'resumed'
    else:
        outcome = 'retried'

    try:
        s.wait_for(
            f"SELECT 1 FROM poc_job_runs WHERE slot > '{slot}' AND finished_at IS NOT NULL",
            timeout=INTERVAL + JOB_SECONDS + 30,
        )
        next_ran = True
    except TimeoutError:
        next_ran = False
    record(
        f'P7-grace-{grace}s',
        {
            'victim': victim,
            'grace_seconds': grace,
            'stop_took_seconds': stop_seconds,
            'exit_code': exit_code,
            'killed_at_deadline': exit_code == 137,
            'restarted_as_sole_runner': sole,
            'outcome': outcome,
            'runs_for_occurrence': rows,
            'next_occurrence_ran': next_ran,
        },
    )
    assert len(finished) <= 1, f'occurrence completed more than once: {rows}'
    assert next_ran
