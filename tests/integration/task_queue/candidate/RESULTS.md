# Procrastinate results

Procrastinate 3.10.0 (latest on PyPI, released 2026-09-23), Postgres as the only
broker. The worker runs inside each FastAPI replica. Run on Docker (OrbStack,
ARM64) on 2026-09-28, with other candidates running on the same host. Raw
evidence is in `results/procrastinate.json` and
`results/procrastinate-no-stalled-retry.json`. P7 was run on 2026-09-28, after the shutdown fix below.

| Check | `candidate/` (with stalled-job retry) | `candidate_no_stalled_retry/` |
|---|---|---|
| P1: once per occurrence | Pass. 12 occurrences, no doubles, none missed, both replicas ran jobs | not run |
| P2: clock skew (+2 s on replica-b) | Pass. No doubles, none missed. Measured offset replica-b +2.00 s | not run |
| P3: killed mid-job, restarted after 10 s | `retried` after 82.9 s, on replica-a. Next occurrence ran | `lost`. Next occurrence ran |
| P3: killed mid-job, left dead | `retried` after 88.9 s, on replica-a. Next occurrence ran | `lost`. Next occurrence ran |
| P4: schema through Alembic | Pass. `migrate` exited 0; replicas ran jobs as `poc_app` | not run |
| P6: footprint | No extra workloads. About 37 MiB and under 0.5% CPU per replica, idle | not run |
| P7: rolling deploy, `stop -t 10` | `retried`. Exit 137 (SIGKILL at the deadline); stop took 10.2 s; the job was retried on replica-b | not run |
| P7: rolling deploy, `stop -t 60` | `drained`. Exit 0; stop took 24.4 s; replica-a finished its own run | not run |

The first P3-restarted run errored before the test started because `migrate`
could not connect to Postgres (see harness issues below). A rerun of that test
alone passed, and that rerun is the result shown.

## Setup

- `requirements.txt`: `procrastinate==3.10.0` plus the packages it adds on top of
  the base image, all pinned.
- Migration `migrations/0001_procrastinate_schema.py` runs as the owner (`poc`).
  It runs `SchemaManager.get_schema()`, the same SQL that
  `procrastinate schema --read` prints. It then grants `poc_app` DML on the
  `procrastinate_*` tables, `USAGE, SELECT, UPDATE` on their sequences,
  `EXECUTE` on the 18 functions, and `USAGE` on the enum and composite types.
  The grants are explicit, so they don't depend on the harness's default
  privileges. I checked this in a separate database that had no default
  privileges.
- `app.py`: `App(connector=PsycopgConnector(conninfo=POC_DATABASE_URL))`. In the
  FastAPI lifespan, `app.open_async()` then
  `asyncio.create_task(app.run_worker_async(install_signal_handlers=False, concurrency=4))`.
  On shutdown it cancels the task and waits up to 10 s. Cancelling makes the
  worker stop gracefully and unregister itself.
- Periodic tick: `@app.periodic(cron='* * * * * */10')`. Cron takes an optional
  6th column for seconds. `*/60` is out of range there, so the 60 s variant uses
  plain `* * * * *`. The task gets `timestamp` (the scheduled Unix time) and
  passes it as the slot.
- Stalled-job retry: the documented recipe (`get_stalled_jobs()` then
  `retry_job()`, with `queueing_lock`), set to run every minute instead of the
  docs' every 10 minutes. `POC_STALLED_RETRY=0` turns it off for the comparison
  variant, which reuses the same image.

## Graceful shutdown (P7)

On SIGTERM, uvicorn runs the lifespan shutdown, which cancels the worker task.
Procrastinate treats that cancel as a graceful stop. It stops fetching jobs,
waits for running jobs up to `shutdown_graceful_timeout` (default `None`, meaning
no limit), then unregisters the worker. I kept that default, and the lifespan
waits for the worker with no timeout of its own.

- **Grace period long enough:** the job finishes and the replica exits 0
  (`drained`).
- **SIGKILL at the deadline:** the job stays `doing` and its worker row stays in
  place. After 30 s without a heartbeat, the stalled-job retry re-queues it on
  the other replica (`retried`). Without the retry task, this job is lost, as in
  P3.

I avoided two other setups:

- **The docs' FastAPI example**, which waits with `asyncio.wait_for(worker,
  timeout=10)`. After 10 s the lifespan gives up and closes the connection pool
  while the job is still running. The worker then fails to record the job's
  status (`AppNotOpen`), so the job stays `doing` and is only recovered by the
  stalled-job retry. A first P7 run with that pattern gave `retried` for both
  grace periods, including a clean exit 0 after 10.7 s at `-t 60`. See
  `results/procrastinate-docs-wait10.json`.
- **A finite `shutdown_graceful_timeout`.** When it expires, the job is marked
  `aborted`. `get_stalled_jobs` only looks at jobs in `doing`, so the stalled-job
  retry never sees it, and the occurrence is lost unless the task also has a
  `retry` policy. Procrastinate retries shutdown-aborted jobs only when the task
  has one. Leaving the timeout unset and letting the orchestrator's SIGKILL do
  the cut-off keeps every cut-off job recoverable.

## Stall threshold

I kept the defaults: a heartbeat every 10 s, and a worker counts as stalled
after 30 s without one (`seconds_since_heartbeat=30`, matching
`stalled_worker_timeout`). That's three missed heartbeats, so a live but slow
worker isn't mistaken for a dead one.

The risk is on the other side. If a live worker's event loop is blocked for more
than 30 s (for example by synchronous code in a task), its heartbeat stops, and
the job gets retried while the first run is still going. That would run the job
twice. Keep tasks async, or raise the threshold.

## What P3 showed

A dead replica's job isn't recovered by Procrastinate itself. Without the retry
task, the job stays in `doing` indefinitely: `lost` in both P3 modes, although
the schedule carries on because the surviving replica defers the next ticks.

With the retry task, recovery takes about 85 s, made up of:

- up to 30 s before the worker counts as stalled;
- a wait for the next minute boundary, when the retry task fires;
- the 30 s job itself.

Restarting the victim doesn't help. The restarted process registers as a new
worker and doesn't reclaim its old job, which is why `restarted` and `replaced`
look the same. The docs' 10-minute cron would push recovery to about 10 minutes.

## Gotchas

- Alembic's `op.execute()` goes through SQLAlchemy `text()`, which treats `:name`
  as a bind parameter. Procrastinate's own `apply_schema` escapes `%`, because
  its connector passes parameters. The migration runs the SQL on the psycopg
  driver connection with no parameters. That allows multiple statements and
  parses no placeholders, and it still runs inside Alembic's transaction.
- The default `concurrency=1` would make the retry task wait behind a 30 s
  tick: both fire on the minute, and after a kill only one replica is left. I
  set concurrency to 4.
- Procrastinate logs nothing under uvicorn's default logging. I didn't configure
  it, so the container logs show no job activity.

## Compared with the desk research

- **Once per period, enforced by the database: confirmed.**
  `procrastinate_periodic_defers` has
  `UNIQUE (task_name, periodic_id, defer_timestamp)`, and
  `procrastinate_defer_periodic_job_v2` inserts with `ON CONFLICT DO NOTHING`.
  Every worker runs a deferrer. P1 and P2 show no doubles, even with a 2 s skew,
  because the slot is the cron timestamp, not the replica's clock.
- **Worker inside FastAPI through `run_worker_async(install_signal_handlers=False)`: confirmed.**
- **Schema ships as plain SQL: confirmed.** The package ships `sql/schema.sql`
  and version-to-version `sql/migrations/*.sql`, split into `pre` and `post`.
  The schema uses unqualified names, so it lands in the connection's
  `search_path` (here, `public`). Upgrading means a new Alembic revision per
  Procrastinate release that applies its migration files, split before and after
  the deploy for zero downtime. Each revision also has to re-grant any new
  functions, unless the database sets default privileges.
- **Stalled jobs need a periodic task you write: confirmed, with one detail.**
  `get_stalled_jobs` also returns `doing` jobs whose worker row is gone, because
  a starting worker prunes stale workers and `worker_id` becomes NULL. So jobs
  are still found after their worker has been pruned. Recovery time depends on
  how often the retry task runs; the documented 10-minute cron is slow.

## P5 — Kubernetes (kind), 2-replica Deployment

| Check | Result |
|---|---|
| P1 once per occurrence | Pass (12, no doubles, none missed) |
| P3 pod force-deleted, replaced under a new name | `retried` on the surviving pod, through the stalled-job retry |
| P7 `rollout restart`, 10 s grace | `retried` |
| P7 `rollout restart`, 60 s grace | `drained` |

No occurrence completed twice and the schedule continued in every case. Kubernetes' new pod names change nothing for Procrastinate: recovery keys on the stalled worker's heartbeat, not on its identity.
