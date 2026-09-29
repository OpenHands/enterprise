"""P10: Postgres restarts while a job is running.

For a Postgres-backed queue, the database is the broker. Restart it mid-job and
check the job finishes or is retried once, the schedule continues, and nothing
runs twice.
"""

import time

INTERVAL = 60
JOB_SECONDS = 30


def test_p10_postgres_restart(stack, record):
    s = stack(POC_INTERVAL_SECONDS=str(INTERVAL), POC_JOB_SECONDS=str(JOB_SECONDS))
    run_id, slot, runner = s.wait_for(
        'SELECT id, slot, replica FROM poc_job_runs WHERE finished_at IS NULL '
        "AND started_at < now() - interval '5 seconds' LIMIT 1",
        timeout=INTERVAL + 60,
    )[0]
    restarted = time.monotonic()
    s.compose('restart', '-t', '5', 'postgres')
    outage_seconds = round(time.monotonic() - restarted, 1)

    def finished_for(where: str, timeout: float) -> bool:
        try:
            s.wait_for(
                f'SELECT 1 FROM poc_job_runs WHERE {where} AND finished_at IS NOT NULL',
                timeout=timeout,
            )
            return True
        except TimeoutError:
            return False

    occurrence_done = finished_for(f"slot = '{slot}'", INTERVAL * 2 + JOB_SECONDS)
    next_ran = finished_for(f"slot > '{slot}'", INTERVAL * 2 + JOB_SECONDS)
    rows = s.sql(
        f"SELECT id, replica, finished_by FROM poc_job_runs WHERE slot = '{slot}' ORDER BY id"
    )
    finished = [r for r in rows if r[2]]
    doubles = s.sql(
        'SELECT slot, count(*) FROM poc_job_runs WHERE finished_at IS NOT NULL '
        'GROUP BY slot HAVING count(*) > 1'
    )
    running = s.compose('ps', '--status', 'running', '--services').split()
    record(
        'P10',
        {
            'runner': runner,
            'restart_seconds': outage_seconds,
            'occurrence_finished': occurrence_done,
            'outcome': (
                'finished'
                if [r[0] for r in finished] == [run_id]
                else 'retried'
                if finished
                else 'lost'
            ),
            'runs_for_occurrence': rows,
            'next_occurrence_ran': next_ran,
            'doubles_any_occurrence': doubles,
            'services_running_after': sorted(running),
        },
    )
    assert len(finished) <= 1 and not doubles, rows
    assert next_ran, 'schedule stopped after the Postgres restart'
