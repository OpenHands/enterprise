# APScheduler 3.x with the run-once guard

APScheduler 3.11.3 (`AsyncIOScheduler`, default in-memory job store) in each of
2 FastAPI replicas. Each replica fires every occurrence, and `claim()` in
Postgres picks one to run it. `candidate_unguarded/` runs the same image with
`POC_GUARD=0`.

## Results

**The harness suite has not run yet.** The base image doesn't build from a
clean checkout (see "Harness issue" below), so every test errors in the
`base_image` fixture before starting a stack. Fill this table in from
`results/apscheduler.json` and `results/apscheduler-unguarded.json` once that
is fixed.

| Check | `apscheduler` | `apscheduler-unguarded` (P1 only) |
|---|---|---|
| P1: once per occurrence | not run | not run (expected: doubles) |
| P2: clock skew (+2 s on replica-b) | not run | n/a |
| P3: killed mid-job, restarted | not run (expected: `lost`) | n/a |
| P3: killed mid-job, left dead | not run (expected: `lost`) | n/a |
| P4: schema through Alembic | not run | n/a |
| P6: footprint | not run | n/a |

A manual smoke run went through the harness compose files with the existing
`tq-poc-base` image: 10-second interval, 3 occurrences. Each slot had one claim
and one run, and the runs alternated between replica-a and replica-b. `migrate`
exited 0, and the replicas ran as `poc_app` with no DDL.

## Setup

- `requirements.txt`: `APScheduler==3.11.3` and its one dependency,
  `tzlocal==5.4.4`. Nothing else is needed: no broker, no extra workload, no
  schema. `migrations/` is empty.
- The job is defined in code. Each replica calls `add_job` in the FastAPI
  lifespan and shuts the scheduler down on exit.
- Trigger: `CronTrigger(second='*/{POC_INTERVAL_SECONDS}', timezone='UTC')`.
  With an interval of 60, `*/60` means second 0 only, so the job fires on the
  wall-clock minute. This only works for intervals that divide 60.
- Guard code: 2 lines in the job (`if GUARD and not await claim(...): return`),
  plus the harness's 10-line `claim()` and the `poc_job_claims` table. In
  production that table needs an Alembic migration and a retention policy.

## Job options

- `misfire_grace_time = INTERVAL // 2 - 1` (4 s at 10 s, 29 s at 60 s). The slot
  comes from `slot_for()`, which rounds to the nearest occurrence. A run that
  starts more than half an interval late would round to the next slot, so it is
  dropped instead. With an in-memory store, a misfire can only come from a
  blocked event loop. A restart doesn't cause one, because the schedule is
  recomputed from "now" and missed occurrences aren't replayed.
- `coalesce=True`: if several occurrences are overdue at once, run only one. This
  has little effect in practice, because the grace time already drops anything
  that late.
- `max_instances=1` (the default, set explicitly): a replica never overlaps
  itself. If a run is still going when the next occurrence fires, that replica
  skips it. The other replica can still claim it, because the guard is per slot.

## Gotchas

- 3.x doesn't pass the scheduled run time to the job function. It appears only
  in the executor log line and in `EVENT_JOB_SUBMITTED` events. So the slot comes
  from `slot_for()`, which works as long as replica clocks are within half an
  interval of each other.
- A graceful stop (SIGTERM) also loses the in-flight run. Uvicorn cancels the
  running coroutine (`CancelledError` inside `finish_run`), and
  `scheduler.shutdown()` doesn't wait for asyncio jobs. In the smoke run,
  stopping replica-a mid-job left its run unfinished, the same outcome as a
  crash.

## P3 and the known ceiling

A crash between `claim()` and `finish_run` loses that occurrence. The claim row
stays, so no replica can run the occurrence again, and nothing retries it. P3
should record `lost` for both the restarted and the replaced case, with
`next_occurrence_ran: true`, because the surviving replica keeps its own
schedule. Recovering lost runs would need a lease with expiry on the claim, or
a real queue.

## Desk research

- Confirmed: the 3.x FAQ says "Sharing a persistent job store among two or more
  processes will lead to incorrect scheduler behavior like duplicate execution
  or the scheduler missing jobs". It recommends one dedicated scheduler process,
  reached over RPyC, gRPC or HTTP. Hence the in-memory store and the external
  guard.
- Confirmed: 4.0 is still alpha on PyPI. The latest release is 4.0.0a6
  (2025-04-27). The latest stable release is 3.11.3 (2026-06-28).

## Harness issue

`harness/Dockerfile` runs `COPY requirements.txt`, but `harness/requirements.txt`
isn't in commit 18cbd4248. The repo `.gitignore` (line 28, `requirements.txt`)
ignores it, so it was never added. In a fresh worktree, `docker build harness`
fails with `"/requirements.txt": not found`, and conftest's `base_image` fixture
errors out every test. The existing `tq-poc-base` image contains the file:
alembic 1.16.5, fastapi 0.118.0, psycopg[binary] 3.2.10, sqlalchemy 2.0.43,
uvicorn 0.37.0. The fix belongs in the harness: force-add the file, or add a
`!` exception to the ignore rule.
