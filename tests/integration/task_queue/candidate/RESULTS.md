# DBOS results

DBOS Python 3.1.0 (latest on PyPI, 2026-09-28), open source only, no Conductor.
Two FastAPI replicas each launch DBOS in the lifespan against Postgres as
`poc_app`. Run on Docker 29.4.0 (OrbStack, ARM64), with other candidates running
on the same host. Raw evidence is in `results/dbos*.json`.

| Check | `dbos` (default executor ID) | `dbos-fixed-executor` (ID = hostname) | `dbos-api-recovery` (ID = hostname + peer resume) |
|---|---|---|---|
| P1: once per occurrence | Pass: 12 occurrences, no doubles, none missed | not run | not run |
| P2: clock skew (+2 s on replica-b) | Pass. Measured offset: replica-b +2.00 s | not run | not run |
| P3: killed, restarted after 10 s | `resumed` after 48.6 s | `resumed` after 49.4 s | `resumed` after 48.8 s |
| P3: killed, left dead | **`lost`** | **`lost`** | `resumed` after 72.3 s |
| P4: schema through Alembic | Pass. `migrate` exited 0; replicas ran as `poc_app` with `run_migrations: False` | not run | not run |
| P6: footprint | No extra workloads. About 60 MiB and 1.1% CPU per idle replica | not run | not run |
| P7: `stop -t 10` | `lost`. Exit 137 after 10.7 s | `lost`. Exit 137 after 10.6 s | not run |
| P7: `stop -t 60` | `drained`. Exit 0 after 26.0 s | `drained`. Exit 0 after 26.2 s | not run |

In every P3 run the next occurrence ran, and no occurrence finished twice.
`resumed` means the killed run's `start_run` row was finished by the recovery,
with no second start row. The `start_run` step's checkpoint was replayed.

## Setup

- `requirements.txt`: `dbos==3.1.0` plus the four packages it adds to the base
  image, all pinned. No extra images or services.
- Migration `migrations/0001_dbos_system_schema.py` runs the documented CLI,
  `dbos migrate -s $POC_MIGRATE_URL -r poc_app`, as the owner role. `-r` is
  DBOS's own grant step. It grants `USAGE` on schema `dbos`, `ALL` on its tables
  and sequences, `EXECUTE` on its functions, and matching default privileges.
  It does not grant `CREATE` on the schema. The call runs inside
  `autocommit_block()` because DBOS's migrations use `CREATE INDEX
  CONCURRENTLY` on their own connection.
- Replicas set `run_migrations: False` (that is the exact 3.x config key), so
  launch only verifies the schema.
- Schedule: `DBOS.apply_schedules_async` after `DBOS.launch()`, cron
  `*/10 * * * * *` or `0 */1 * * * *`. Six fields, with seconds first. The
  workflow receives DBOS's scheduled time and passes it as `slot`. Its body is
  two `@DBOS.step`s: `start_run`, then `finish_run`.
- Variants are chosen by env passthrough in `compose.yaml`:
  `POC_DBOS_EXECUTOR=hostname` sets `executor_id` from the hostname, and
  `POC_DBOS_PEER_RECOVERY=1` turns on peer resume.

## Gotchas

- In 3.x, schedules are rows in `dbos.workflow_schedules`, created with
  `create_schedule` or `apply_schedules`. I found no `@DBOS.scheduled`
  decorator in 3.1.0. `apply_schedules` upserts by name, so every replica can
  call it at boot.
- The default executor ID is the literal string `"local"` (or `DBOS__VMID`),
  not a random ID. Every replica in variant 1 shares it. A restarting replica
  therefore re-enqueues every PENDING `local` workflow, including ones another
  live replica is running.

## What P3 showed

- Recovery re-enqueues instead of running the workflow in place
  (`_recovery.py`: "Recovery re-enqueues rather than executing directly"). In
  both `restarted` runs, the restarted replica logged "Recovering 1 workflows",
  but the other replica dequeued the workflow and finished it. So "the restarted
  process resumes it" really means "the restart makes it runnable again, and any
  replica may pick it up."
- `replaced` is `lost` with either executor ID setting. Nothing recovers a
  PENDING workflow until a process with the same executor ID starts again.
  A StatefulSet-style fixed ID only helps if that same pod comes back.
- Variant 3 used only documented public API:
  `DBOS.list_workflows_async(status='PENDING', dequeued_before=...)`, then
  `DBOS.resume_workflow_async(id)` for rows whose `executor_id` is not this
  process's. Both are in the Python reference, docs.dbos.dev/python/reference/contexts.
  The docs present `resume_workflow` for cancelled workflows, workflows over
  their recovery limit, or enqueued ones. Using it on a dead executor's PENDING
  workflow is my extension, although the code accepts any non-terminal status.
  DBOS gives no liveness signal without Conductor. I used staleness instead:
  dequeued more than 1.5 × the job length ago. A live job that runs longer than
  that would run twice, so this is not production-safe as written.
- The only API that takes executor IDs is `DBOS._recover_pending_workflows`.
  It is underscore-prefixed, its docstring says "Internal", and it is
  undocumented. It is what the Conductor client calls
  (`_conductor/conductor.py`). I did not use it.

## Against the desk research

- Idempotency key: confirmed. The workflow ID is
  `sched-{schedule_name}-{scheduled_time.isoformat()}`, and a replica enqueues
  only if that ID has no status yet. The scheduled-workflows tutorial says the
  same. P1 and P2 passed with no doubles.
- Schema: confirmed. `run_migrations` defaults to True. Set to False, launch
  only verifies the schema. `dbos migrate` applies it, and `-r` grants the app
  role. The Kubernetes guide recommends the same split through
  `dbosctl sysdb migrate`.
- Recovery: confirmed, and slightly worse than stated. The current workflow
  recovery page says each executor "only recovers pending workflows assigned
  to that executor ID", so replicas "never recover each other's workflows".
  Automatic cross-executor recovery is a Conductor feature, and Conductor needs
  a paid license for production. I did not find the exact quote "required for
  correct workflow recovery in applications that use more than one process" on
  the current pages. The Kubernetes guide recommends a plain Deployment and says
  each replica "should have a unique executor ID (which is automatically
  assigned when using DBOS Conductor)". Without Conductor, those two pieces of
  advice work against each other.

## Shutdown (P7)

- Configured: the documented `DBOS.destroy(workflow_completion_timeout_sec=50)`
  in the lifespan shutdown, set just under a 60 s termination grace period. It
  is called through `asyncio.to_thread`. Nothing else was added.
- Default, `workflow_completion_timeout_sec=0`: destroy returns at once. The
  process exits 0 in under a second, and the workflow stays PENDING with its
  `start_run` row unfinished (`results/dbos-no-drain.json`: `lost` at both
  grace periods). Nothing recovers it unless a process with the same executor
  ID starts, as in P3.
- With a timeout, destroy first stops the queue and scheduler threads, so no
  new work is taken. It then polls until active workflows finish or the timeout
  expires. It does not cancel running workflows. After the timeout they are
  left PENDING and can no longer checkpoint.
- Gotcha: `launch()` called from the lifespan makes uvicorn's loop DBOS's
  "main loop", so async workflows run on it. A plain, blocking
  `DBOS.destroy(...)` there freezes the workflow it is waiting for. That run
  waited the full 50 s, exited 0 after 51.5 s, and the job was `lost` at 60 s
  grace (`results/dbos-sync-destroy.json`). Calling destroy off the loop fixes
  it. The docs don't cover this, and 3.1.0 has no `destroy_async`.
- `-t 10` is shorter than the remaining job, so SIGKILL leaves the workflow
  PENDING, and P7 does not restart the replica. That result is `lost` in both
  variants, the same as P3 `replaced`.

## Harness issue

The P3 variant runs first hit a Postgres readiness race: the Unix-socket
`pg_isready` check passed during the init server. The harness now checks over
TCP (9ec42e2d2), and the candidate's temporary override is removed.

## P5 — Kubernetes (kind), 2-replica Deployment

| Check | Default executor ID (`"local"`) | Per-pod executor ID (`POC_DBOS_EXECUTOR=hostname`) |
|---|---|---|
| P1 once per occurrence | Pass (12, no doubles, none missed) | Pass |
| P3 pod force-deleted, replaced under a new name | `resumed` by the replacement pod | `lost` |
| P7 `rollout restart`, 10 s grace | `resumed` | `lost` |
| P7 `rollout restart`, 60 s grace | `drained` | `drained` |

**The default executor ID re-runs in-flight workflows on every rollout.** In the 60 s rollout, the old pod was still running the workflow and finished it, while both new pods logged `Recovering 1 workflows` within a second of starting. Every replica shares the executor ID `"local"`, so a starting pod recovers every PENDING `local` workflow, including those a live pod is still executing. The stub's final step is idempotent, so the results table shows no double completion. A non-idempotent step would run twice concurrently. Without Conductor, the choice is between the shared ID (recovers dead pods' work, duplicates live pods' work on rollout) and per-pod IDs (no duplication, loses a replaced pod's work).
