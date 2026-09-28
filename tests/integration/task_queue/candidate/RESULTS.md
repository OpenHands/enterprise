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
- Harness race: see "Harness issue" below. I worked around it with a
  healthcheck override in `candidate/compose.yaml`.

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

## Harness issue

The shared Postgres healthcheck, `pg_isready -U poc -d poc`, uses the Unix
socket. It passes against the entrypoint's temporary init server, which listens
only on the socket. Under load, `migrate` then got "Connection refused" over
TCP, which failed the first `restarted` runs of variants 2 and 3.
`candidate/compose.yaml` overrides the check with `pg_isready -h 127.0.0.1`.
That fix belongs in the harness `compose.yaml`. In variant 1, P1 ran before the
override was added. The override only affects start-up readiness.
