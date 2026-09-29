"""P11: a poison job that kills its worker every time it runs.

A crashed worker's job is retried by stalled-job recovery. A job that crashes
every worker it lands on can crash-loop the whole pool unless retries stop.
The candidate queues one poison job (POC_POISON=1) next to the normal 10 s
tick; pass means poison attempts stop growing and the tick keeps running.
"""

import time

import pytest

INTERVAL = 10
OBSERVE = 360


def test_p11_poison_job(stack, record):
    s = stack(POC_INTERVAL_SECONDS=str(INTERVAL), POC_JOB_SECONDS='1', POC_POISON='1')
    try:
        s.wait_for("SELECT 1 FROM poc_job_runs WHERE job = 'poison'", timeout=120)
    except TimeoutError:
        pytest.skip('candidate has no poison job (POC_POISON)')
    start = int(s.sql('SELECT extract(epoch FROM now())::bigint')[0][0])
    time.sleep(OBSERVE)
    poison = [
        int(r[0])
        for r in s.sql(
            'SELECT extract(epoch FROM started_at)::bigint FROM poc_job_runs '
            "WHERE job = 'poison' ORDER BY id"
        )
    ]
    late = [t for t in poison if t > start + OBSERVE - 120]
    ticks = int(
        s.sql(
            "SELECT count(DISTINCT slot) FROM poc_job_runs WHERE job = 'tick' "
            f'AND finished_at IS NOT NULL AND slot > to_timestamp({start})'
        )[0][0]
    )
    expected = OBSERVE // INTERVAL
    restarts = {}
    for cid in s.compose('ps', '-q').split():
        name, count = (
            __import__('subprocess')
            .run(
                ['docker', 'inspect', '-f', '{{.Name}} {{.RestartCount}}', cid],
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.split()
        )
        restarts[name.strip('/')] = int(count)
    record(
        'P11',
        {
            'poison_attempts': len(poison),
            'poison_attempts_last_2_min': len(late),
            'tick_occurrences': ticks,
            'tick_expected': expected,
            'container_restarts': restarts,
            'observed_seconds': OBSERVE,
        },
    )
    assert not late, (
        f'poison job still retrying after {OBSERVE - 120} s: {len(poison)} attempts'
    )
    assert ticks >= 0.8 * expected, f'schedule starved: {ticks} of {expected} ticks'
