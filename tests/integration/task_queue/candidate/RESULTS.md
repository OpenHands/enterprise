# Celery + Celery Beat results

Celery 5.6.3 (latest on PyPI, 2026-09-28), kombu 5.6.2, redis-py 6.4.0, Redis
7.4.1 (the chart's bitnami subchart appVersion) with `--save "" --appendonly no`.
RedBeat 2.4.2 for the second variant. Run on Docker (OrbStack, ARM64),
2026-09-28, with other candidates running on the same host. Raw evidence:
`results/celery.json` and `results/celery-redbeat.json`.

| Check | `candidate/` (one Beat) | `candidate_redbeat/` (beat-a + beat-b, RedBeat) |
|---|---|---|
| P1 — once per occurrence | Pass: 12 occurrences, no doubles, none missed | Pass: 12 occurrences, no doubles, none missed |
| P2 — clock skew (+2 s) | Skipped: single scheduler | Pass: 12 occurrences, no doubles, none missed. beat-b (clock +2 s, confirmed in its log timestamps) held the lock and sent all 12; beat-a stood by. Rerun after the harness's libfaketime fix |
| P3 — worker killed mid-job, restarted | `lost` (victim worker-a); next occurrence ran | `lost` (victim worker-a); next occurrence ran |
| P3 — worker killed mid-job, left dead | `lost` (victim worker-a); next occurrence ran | `lost` (victim worker-b); next occurrence ran. First attempt hit the harness's old Postgres healthcheck race (`migrate` exit 1); rerun after rebasing onto the fix |
| P4 — schema through Alembic | Pass: no tables, `migrate` is a no-op, exit 0 | Pass: same |
| P6 — footprint | Extra: `beat`, `redis`, `worker-a`, `worker-b`. Idle: worker ~80 MiB each (2 pool processes), beat ~41 MiB, redis ~14 MiB, replica ~28 MiB | Extra: `beat-a`, `beat-b`, `redis`, `worker-a`, `worker-b`. Idle: worker ~80 MiB each, beat ~40 MiB each, redis ~6 MiB |

## Setup

- Services: `redis`, `replica-a`/`replica-b` (bare FastAPI, schedule nothing),
  `worker-a`/`worker-b` (`celery -A tasks worker --concurrency=2`, hostname =
  service name), and `beat` (`celery -A tasks beat`) or `beat-a`/`beat-b`
  (`celery -A redbeat_app beat`).
- Inputs: a broker URL (`redis://redis:6379/0`) and the schedule
  (`beat_schedule` with `timedelta(seconds=POC_INTERVAL_SECONDS)`). No database
  schema; `migrations/` is empty.
- Delivery settings: `task_acks_late=True`, `task_reject_on_worker_lost=True`,
  and `worker_prefetch_multiplier=1` (the documented pairing for late acks).
- RedBeat: `beat_scheduler='redbeat.RedBeatScheduler'`, plus
  `beat_max_loop_interval=5` and `redbeat_lock_timeout=30`. With the defaults
  (300 s loop, lock = 5 × loop = 1500 s) a standby Beat takes up to 25 minutes to
  take over. With 30 s, killing the lock holder in a manual test let the standby
  take over 29 s later, and it fired the overdue entry immediately.

## Gotchas

- **No scheduled time reaches the task.** Beat calls `apply_async` with no ETA
  and no header carrying the due time, so `tick` uses `slot_for()` at run time.
  Two consequences:
  - Beat's `timedelta` schedule is relative to Beat's start, not aligned to the
    wall clock (`relative=True` only rounds to whole seconds for sub-minute
    intervals, and `crontab` has minute resolution). If Beat happens to start
    near the half-interval point, `slot_for()` rounding can flip between
    neighbours as Beat drifts (~1–2 ms per tick seen), giving a double and a
    miss. One smoke run started 5.09 s into an interval. Not seen in the
    recorded P1 runs.
  - A backlog collapses into one slot. After a worker outage, every queued
    `tick` runs on catch-up and maps to the current slot (seen in the Redis
    restart experiment: three runs for one slot). Celery's `expires` option on
    the schedule entry is the supported way to drop stale periodic messages; not
    set here.
- `broker_connection_retry_on_startup=True` is set to silence the 5.x
  deprecation warning; behaviour is unchanged.

## What P3 showed

The victim was always a worker (worker-a, except worker-b in the RedBeat left-dead rerun) (Beat doesn't run jobs). `docker kill` sends
SIGKILL to the whole worker container, so the main process dies with its pool
child. `task_reject_on_worker_lost` only helps when a pool child dies and the
main process survives to requeue the message; here nothing survives. The
message stays in Redis's `unacked` hash, and kombu restores it only once it is
older than the visibility timeout (default 3600 s, confirmed in kombu 5.6.2
source). So within the test window the occurrence is `lost`, restarted or not.
A restarted worker does not reclaim it early. Inference, not measured: about an
hour later the message is redelivered and runs again under whatever slot
`slot_for()` gives at that time, so a lost run comes back as an extra run in a
different slot. Lowering `visibility_timeout` below the longest task duration
makes Redis redeliver tasks that are still running (a documented Celery
caveat), so it has to stay above the longest job.

## Redis restart with queued tasks (manual)

Standard variant: stopped both workers, let Beat queue three `tick` messages
(`LLEN celery` = 3), ran `docker compose restart redis`. Afterwards
`LLEN celery` = 0: all three queued tasks were gone, with no error anywhere.
Beat and the workers reconnected on their own and later ticks ran. Those three
occurrences never ran.

RedBeat variant: restarting Redis wipes the lock, the schedule entries and the
queue. The Beat holding the lock crashed at its next tick with
`LockNotOwnedError: Cannot extend a lock that's no longer owned` and exited.
Compose here has no restart policy, so no Beat was left running. On Kubernetes
the pod would restart and re-create the static entries from `beat_schedule`.

## Against the desk research

- Beat must be a single instance: confirmed by design. Plain Beat has no
  coordination; the standard variant runs one.
- RedBeat adds a Redis lock for standby Beats: confirmed. Only the lock holder
  sends tasks, and failover works. But its default timings make failover take up
  to 25 minutes, and a Redis restart crashes the active Beat.
- No native asyncio: confirmed. Celery 5.6.3 has no `asyncio` or coroutine
  handling in its source; tasks are sync, so `run_stub` runs its own event loop.
- Early acks by default: confirmed (`acks_late` defaults to False). But
  `acks_late` + `task_reject_on_worker_lost` does not give at-least-once when
  the whole worker dies on the Redis broker, not within an hour: the retry waits
  for the visibility timeout.
- Visibility timeout defaults to 1 hour: confirmed (`visibility_timeout = 3600`
  in kombu's Redis transport).
- Our bundled Redis runs with persistence off: partly. The chart sets
  `master.persistence.enabled: false` (no PVC), but the bitnami subchart's
  default `commonConfiguration` keeps `appendonly yes` (with `save ""`), writing
  AOF to an emptyDir. The queue survives a Redis container restart inside the
  same pod, and is lost when the pod is deleted or rescheduled. These tests ran
  with no AOF and no RDB, which matches the pod-replacement case.

## Note on P2

The first P2 run was invalid: with `FAKETIME_DONT_FAKE_MONOTONIC=1`, libfaketime
made Python's `time.sleep` raise `OSError: [Errno 22]`, so beat-b crashed on
start. After the harness fix (`"0"`, plus a check that the skewed service is
still running), beat-b stayed up. Only the lock holder schedules, so the skew
can't produce a second firing. The one case this run didn't exercise is a lock
handover between Beats with different clocks. RedBeat keeps `last_run_at` in
Redis, so a skewed successor could fire an occurrence early.
