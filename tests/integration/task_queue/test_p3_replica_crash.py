"""P3: a replica dies mid-job. Record whether the run is lost, retried or resumed."""

import time

import pytest

from conftest import MANIFEST

INTERVAL = 60
JOB_SECONDS = 30


@pytest.mark.parametrize('restart', [True, False], ids=['restarted', 'replaced'])
def test_p3_replica_crash(stack, record, restart):
    s = stack(POC_INTERVAL_SECONDS=str(INTERVAL), POC_JOB_SECONDS=str(JOB_SECONDS))
    run_id, slot, victim = s.wait_for(
        'SELECT id, slot, replica FROM poc_job_runs '
        "WHERE finished_at IS NULL AND started_at < now() - interval '5 seconds' LIMIT 1",
        timeout=INTERVAL + 60,
    )[0]
    assert victim in MANIFEST['job_services'], f'{victim} is not a declared job service'
    killed_at = time.monotonic()
    s.compose('kill', victim)
    if restart:
        # Same service name and hostname: what a Compose restart policy does.
        time.sleep(10)
        s.compose('start', victim)
    # 'replaced' leaves the victim dead: a Kubernetes pod replaced under a new name.

    try:
        s.wait_for(
            f"SELECT 1 FROM poc_job_runs WHERE slot = '{slot}' AND finished_at IS NOT NULL",
            timeout=INTERVAL * 2 + JOB_SECONDS,
        )
        recovered_after = round(time.monotonic() - killed_at, 1)
    except TimeoutError:
        recovered_after = None

    rows = s.sql(
        f"SELECT id, replica, finished_by FROM poc_job_runs WHERE slot = '{slot}' ORDER BY id"
    )
    finished = [r for r in rows if r[2]]
    if not finished:
        outcome = 'lost'
    elif [r[0] for r in finished] == [run_id] and len(rows) == 1:
        outcome = 'resumed'  # the original run was completed, with no second start
    else:
        outcome = 'retried'
    # The schedule must keep going on the surviving replica either way.
    try:
        s.wait_for(
            f"SELECT 1 FROM poc_job_runs WHERE slot > '{slot}' AND finished_at IS NOT NULL",
            timeout=INTERVAL + JOB_SECONDS + 30,
        )
        next_ran = True
    except TimeoutError:
        next_ran = False
    record(
        f'P3-{"restarted" if restart else "replaced"}',
        {
            'victim': victim,
            'outcome': outcome,
            'recovered_after_seconds': recovered_after,
            'runs_for_occurrence': rows,
            'next_occurrence_ran': next_ran,
        },
    )
    assert len(finished) <= 1, f'occurrence completed more than once: {rows}'
    assert next_ran
