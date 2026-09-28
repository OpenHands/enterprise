# APScheduler 3.x with the run-once guard

APScheduler 3.11.3 (`AsyncIOScheduler`, default in-memory job store) in each of
2 FastAPI replicas. Each replica fires every occurrence, and `claim()` in
Postgres picks one to run it. `candidate_unguarded/` runs the same image with
`POC_GUARD=0`.

## Results

Run on 2026-09-28 on Docker (OrbStack, ARM64), with other candidates running
on the same host. Raw evidence is in `results/apscheduler.json` and
`results/apscheduler-unguarded.json`.

| Check | `apscheduler` (guarded) | `apscheduler-unguarded` |
|---|---|---|
| P1: once per occurrence | Pass: 12 occurrences, no doubles, none missed, both replicas ran jobs | **Fails as intended:** all 12 occurrences ran on both replicas |
| P2: clock skew (+2 s on replica-b) | Pass: no doubles, none missed. Measured offset +2.00 s. Only replica-b ran jobs | not run |
| P3: killed mid-job, restarted after 10 s | `lost`. The run never finished, the next occurrence ran | not run |
| P3: killed mid-job, left dead | `lost`. Same; the surviving replica kept the schedule | not run |
| P4: schema through Alembic | Pass: `migrate` exited 0, no candidate tables, jobs ran as `poc_app` | not run |
| P7: rolling deploy, stop `-t 10` | `lost`. Exit 0 after 0.4 s: the job was cancelled at SIGTERM; the next occurrence ran | not run |
| P7: rolling deploy, stop `-t 60` | `lost`. Exit 0 after 0.7 s: same, the grace period went unused | not run |
| P6: footprint | No extra workloads. About 35.5 MiB and under 0.4% CPU per replica, idle | not run |

## Setup

- `requirements.txt`: `APScheduler==3.11.3` and its one dependency,
  `tzlocal==5.4.4`. Nothing else is needed: no broker, no extra workload, no
  schema. `migrations/` is empty.
- The job is defined in code. Each replica calls `add_job` in the FastAPI
  lifespan and shuts the scheduler down on exit.
- Trigger: `CronTrigger(second='0,10,20,30,40,50')` for 10 s, or `second='0'`
  for 60 s, in UTC. The list is built from `POC_INTERVAL_SECONDS`, so it only
  works for intervals that divide 60.
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

- `CronTrigger(second='*/60')` raises `ValueError: the step value (60) is
  higher than the total range of the expression (59)` when the app starts. The
  first full run used `*/{INTERVAL}`: P1 and P2 passed at 10 s, but both P3
  cases failed because neither replica booted at 60 s. An explicit list of
  seconds fixed it.
- Under a fixed skew, the replica whose clock is ahead fires first every time,
  so it wins every claim. In P2 replica-b ran all 12 occurrences. The guard
  keeps runs correct but doesn't spread work across replicas.

- 3.x doesn't pass the scheduled run time to the job function. It appears only
  in the executor log line and in `EVENT_JOB_SUBMITTED` events. So the slot comes
  from `slot_for()`, which works as long as replica clocks are within half an
  interval of each other.
- A graceful stop (SIGTERM) also loses the in-flight run. Uvicorn cancels the
  running coroutine (`CancelledError` inside `finish_run`), and
  `scheduler.shutdown()` doesn't wait for asyncio jobs. In a manual run,
  stopping replica-a mid-job left its run unfinished, the same outcome as a
  crash.

## Graceful shutdown (P7)

APScheduler 3.11 has no supported way to wait for asyncio jobs. `shutdown()`
already defaults to `wait=True`, but `AsyncIOExecutor.shutdown()` cancels every
pending job future regardless. Its source says: "There is no way to honor
wait=True without converting this method into a coroutine method". So the
candidate keeps the default. On SIGTERM, uvicorn runs the lifespan exit, the
scheduler cancels the in-flight job (`CancelledError`), and the process exits 0
within a second. The run is `lost` whatever the grace period. The claim row
stays, so the other replica doesn't pick the run up either.

The only documented executor that honors `wait=True` is `ThreadPoolExecutor`.
Using it means turning the job into a synchronous function, and
`shutdown(wait=True)` then blocks the event loop until the threads finish. I
didn't test it. The harness's `POC_DRAIN` control shows what draining would
give: `lost` (exit 137) at 10 s and `drained` at 60 s.

## P3 and the known ceiling

A crash between `claim()` and `finish_run` loses that occurrence. The claim row
stays, so no replica can run the occurrence again, and nothing retries it. P3
records `lost` for both the restarted and the replaced case, with
`next_occurrence_ran: true`, because the surviving replica keeps its own
schedule. P3 confirmed it: `lost` in both cases, `next_occurrence_ran: true`, and
one unfinished run row for the killed occurrence. Recovering lost runs would
need a lease with expiry on the claim, or a real queue.

## Desk research

- Confirmed: the 3.x FAQ says "Sharing a persistent job store among two or more
  processes will lead to incorrect scheduler behavior like duplicate execution
  or the scheduler missing jobs". It recommends one dedicated scheduler process,
  reached over RPyC, gRPC or HTTP. Hence the in-memory store and the external
  guard.
- Confirmed: 4.0 is still alpha on PyPI. The latest release is 4.0.0a6
  (2025-04-27). The latest stable release is 3.11.3 (2026-06-28).

## Harness issue

The base commit didn't track `harness/requirements.txt`, because the repo
`.gitignore` ignores `requirements.txt`. The base image couldn't build. This is
fixed on the base branch, which these results were run against.
