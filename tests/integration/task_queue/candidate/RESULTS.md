# Dramatiq + APScheduler results

Dramatiq 2.2.1 with a Redis broker (redis 7.4-alpine, persistence off) and
APScheduler 3.11.3. Run on Docker 29.4.0 (OrbStack, ARM64) on 2026-09-28, with
other candidates running on the same host. Raw evidence is in
`results/dramatiq-single.json` and `results/dramatiq-guarded.json`.

- **3a, single scheduler** (`candidate/`): one `scheduler` service runs
  APScheduler's `BlockingScheduler` and sends one Dramatiq message per occurrence.
- **3b, guarded scheduler** (`candidate_guarded/`): APScheduler's
  `AsyncIOScheduler` runs in the FastAPI lifespan of both replicas. Each replica
  calls `claim(job, slot)` and sends only if it wins.

In both setups `worker-a` and `worker-b` run `dramatiq tasks --processes 1 --threads 8`,
and the actor is `async def` and awaits `stub_job`.

| Check | 3a single scheduler | 3b guarded scheduler |
|---|---|---|
| P1: once per occurrence | Pass. 12 occurrences, no doubles, none missed | Pass. 12 occurrences, no doubles, none missed |
| P2: clock skew (+2 s on replica-b) | Skipped (single scheduler) | Pass. No doubles, none missed. See the P2 note |
| P3: worker killed mid-job, restarted after 10 s | `retried`. Another worker finished it 139 s after the kill | `lost` within the test window |
| P3: worker killed mid-job, left dead | `lost` within the test window. The message had been redelivered to worker-b, but that run hadn't finished when the test took its snapshot | `lost` within the test window |
| P3: the next occurrence still ran | Yes, both cases | Yes, both cases |
| P4: schema through Alembic | Pass. `migrate` exited 0 with no revisions, and no candidate tables | Pass. Same |
| P6: extra workloads | `redis`, `scheduler`, `worker-a`, `worker-b` | `redis`, `worker-a`, `worker-b` |
| P6: idle memory | redis 11 MiB, scheduler 28 MiB, about 41 MiB per worker, about 40 MiB per replica | redis 7 MiB, about 42 MiB per worker, 42 to 44 MiB per replica |
| P7: rolling deploy, 10 s grace | `retried`. Killed at the deadline (exit 137, stop took 10.5 s). Another worker ran it again and finished | `lost` within the test window. Killed at the deadline (exit 137, stop took 10.5 s) |
| P7: rolling deploy, 60 s grace | `drained`. Exit 0, stop took 29.0 s | `drained`. Exit 0, stop took 28.9 s |
| P7: the next occurrence still ran | Yes, both cases | Yes, both cases |

## Setup

- Inputs: a Redis URL (`POC_REDIS_URL`) for replicas, workers and the scheduler.
  There are no schema or migrations. 3b also uses the harness's `poc_job_claims`
  table for the guard.
- `tasks.py` builds the `RedisBroker`, adds the `AsyncIO` middleware, and
  defines the actor and the cron trigger. The same image runs every role:
  `uvicorn app:app` for replicas, `dramatiq tasks` for workers, and
  `python scheduler.py` for the 3a scheduler. `POC_SCHEDULE=1` turns on the
  in-app scheduler for 3b.
- Redis runs with `--save "" --appendonly no`, so queued and in-flight
  messages don't survive a Redis restart.

## Gotchas

- **Async actors need the `AsyncIO` middleware.** It isn't in Dramatiq's default
  middleware. Without it, an `async def` actor fails at call time with "Global
  event loop thread not set". One `broker.add_middleware(AsyncIO())` call fixes it.
- **APScheduler 3.x doesn't pass the scheduled run time to the job**, so the slot
  comes from `slot_for()` at fire time.
- **Cron steps can't exceed the field's range.** `CronTrigger(second='*/60')`
  raises "the step value (60) is higher than the total range". Intervals of a
  minute or more go in the `minute` field.
- **Redelivery after a crash is slow on the Redis broker, and its timing is
  random.** A killed worker's in-flight message stays in that worker's ack set.
  Another process moves it back to the queue only during "maintenance". That
  needs the dead worker's heartbeat to be older than `heartbeat_timeout` (60 s
  by default), and maintenance runs on only `maintenance_chance` = 1000 in a
  million (0.1%) of broker commands. An idle worker process sends about 2
  commands a second, so the expected delay is about 60 s plus several minutes.
  A restarted worker gets a new broker id, so it doesn't reclaim its own old
  messages any faster. Both knobs are documented `RedisBroker` parameters. I
  left them at their defaults.
- **Sleeping on a skewed clock.** The harness now sets
  `FAKETIME_DONT_FAKE_MONOTONIC: "0"`. P2 was rerun with that setting and passed.

## What P3 showed

Dramatiq does not lose the killed run for good, but the recovery time depends
on chance. In the four test runs, one recovery landed inside the harness's
150-second window: 3a restarted, finished 139 s after the kill. A second was
redelivered inside the window but finished after the snapshot: 3a replaced.
Two never came back within the window: both 3b runs. In a separate manual run
(3a, victim left dead, watched for 15 minutes), the message was redelivered
about 6.5 minutes after the kill and finished once on the surviving worker.
The expected outcome is therefore `retried`, after a delay somewhere between
about 1 minute and many minutes. The job must be idempotent, because the first
attempt may have done partial work. The schedule kept running in every case.

## Graceful shutdown (P7)

I used the defaults and configured nothing. `dramatiq` runs as PID 1, since
the compose `command` has no shell wrapper. On SIGTERM it stops fetching,
waits for in-flight actors, and puts prefetched messages that haven't started
back on the queue. The wait is bounded by `--worker-shutdown-timeout`, which
defaults to 600000 ms (10 minutes). That default is longer than any grace
period, so the pod's grace period is what actually limits the wait.

- **60 s grace:** the 30 s job finished, and the worker acked it and exited 0.
  The result is `drained`.
- **10 s grace:** SIGKILL arrives while the job is still running. The message
  is still unacked in the dead worker's ack set, so it is redelivered the same
  way as in P3: after the heartbeat timeout plus random maintenance. That was
  inside the window for 3a (`retried`) and outside it for 3b (`lost` in the
  window, not lost for good).

In production, set the pod's `terminationGracePeriodSeconds` above the longest
job. Lowering `--worker-shutdown-timeout` would not help. The process would
exit sooner, but the message would still wait for maintenance to put it back.

## 3b: a replica dying between `claim` and `send`

Reasoning from the code, not tested: `claim` commits the `poc_job_claims` row
in its own autocommit connection before `tick.send` runs. If the replica dies,
or `send` fails because Redis is down, in that gap, the occurrence is lost with
no trace except an orphaned claim row. The other replica's `claim` returns
False and it sends nothing. Nothing retries it, because the claim is the only
record, and APScheduler's in-memory store has no memory of it. There are two
fixes. Sending before claiming, with the claim moved into the actor so the
workers dedupe, closes the gap. The cost is two messages per occurrence. A
transactional outbox also closes it, but that adds a table and a relay.

## Changes to the desk-research picture

- **No built-in scheduler: confirmed.** The motivation page says to combine
  Dramatiq with APScheduler or Periodiq. The cookbook calls APScheduler "the
  recommended scheduler".
- **Acks late (at-least-once): confirmed.** The docs say tasks "are only ever
  acked when they're done processing", and P3 saw a redelivery. The caveat is
  that on the Redis broker, redelivery depends on heartbeat expiry plus
  probabilistic maintenance, so it takes minutes, not seconds. RabbitMQ, which
  requeues when the consumer connection drops, was not tested.
- **APScheduler 3.x must not share a persistent job store across processes:
  confirmed.** The APScheduler 3.x FAQ answers "You can't" and warns of
  duplicate and missed runs. Both setups use the in-memory store. 3b relies on
  the guard instead.
- **Async actors are capped by the thread count: confirmed from the source.**
  The async actor is wrapped with `async_to_sync`, and the calling worker thread
  blocks on `EventLoopThread.run_coroutine` until the coroutine finishes. So at
  most `--threads` coroutines run at once per process, even though they share
  one event loop.
- **P2 evidence is indirect for this candidate.** The harness measures clock
  offset from `poc_job_runs.fired_at`, which the worker writes, so it reads
  0.00 s for both workers. A manual check on the same overlay showed the skew
  was applied. `date` in replica-b ran 2 s ahead of replica-a, and replica-b won
  all 6 claims, about 1.98 s before each slot.
- **3a has a single point of failure.** If the `scheduler` service is down,
  occurrences are skipped, and nothing notices. With the in-memory store, a
  restarted scheduler doesn't catch up on missed runs.
