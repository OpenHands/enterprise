"""P9: a job that blocks the event loop longer than the stall threshold.

Several real jobs call sync code (``time.sleep``, sync SQLAlchemy) from async
functions. A worker whose loop is blocked cannot send heartbeats, so a candidate
that detects dead workers by heartbeat may re-run a job that is still running.
The stub blocks for 100 s, over three times a 30 s stall threshold and longer
than a once-a-minute recovery cycle.
"""

import time

JOB_SECONDS = 100


def test_p9_blocking_job(stack, record):
    s = stack(
        POC_INTERVAL_SECONDS='60',
        POC_JOB_SECONDS=str(JOB_SECONDS),
        POC_JOB_BLOCKING='1',
    )
    run_id, slot, runner = s.wait_for(
        'SELECT id, slot, replica FROM poc_job_runs WHERE finished_at IS NULL '
        "AND started_at < now() - interval '5 seconds' ORDER BY id LIMIT 1",
        timeout=180,
    )[0]
    started = time.monotonic()
    # Long enough for the first run to finish and any stall recovery to act.
    s.wait_for(
        f"SELECT 1 FROM poc_job_runs WHERE slot = '{slot}' AND id = {run_id} "
        'AND finished_at IS NOT NULL',
        timeout=JOB_SECONDS + 120,
    )
    time.sleep(60)
    rows = s.sql(
        'SELECT id, replica, started_at, finished_at, finished_by FROM poc_job_runs '
        f"WHERE slot = '{slot}' ORDER BY id"
    )
    # A second start while the first run was still going is a concurrent double run.
    overlap = s.sql(
        'SELECT count(*) FROM poc_job_runs b, poc_job_runs a '
        f'WHERE a.id = {run_id} AND b.slot = a.slot AND b.id <> a.id '
        'AND b.started_at < a.finished_at'
    )[0][0]
    finished = [r for r in rows if r[4]]
    record(
        'P9',
        {
            'job_seconds': JOB_SECONDS,
            'first_runner': runner,
            'starts_for_occurrence': len(rows),
            'completions_for_occurrence': len(finished),
            'concurrent_second_starts': int(overlap),
            'runs_for_occurrence': rows,
            'seconds_observed': round(time.monotonic() - started),
        },
    )
    # Any second start is a re-run of the same occurrence, concurrent or not.
    assert len(rows) == 1, f'occurrence started {len(rows)} times: {rows}'
    assert int(overlap) == 0, f'job re-run while still running: {rows}'
