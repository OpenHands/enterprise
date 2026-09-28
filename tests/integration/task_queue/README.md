# Task queue POC harness

Shared harness for comparing ways to run scheduled and background work on
Docker Compose and Kubernetes. Each candidate lives on its own branch and adds
a `candidate/` directory next to this file; the tests here are identical for
every candidate.

## Running

Needs Docker with Compose v2. No database, identity provider or running
OpenHands app is needed.

```bash
uv run --no-sync python -m pytest tests/integration/task_queue \
  --confcutdir=tests/integration/task_queue -q
```

`--confcutdir` keeps the repo's root `conftest.py` from starting its own
PostgreSQL container. Allow about 20 minutes: P3 waits for 60-second
occurrences.

Without a `candidate/` directory, the tests run the harness's negative control,
`harness/naive`: an in-process loop with no coordination. Set `POC_GUARD=1` to
add the run-once guard, and `TQ_RESULTS_NAME` to record that run separately:

```bash
uv run --no-sync python -m pytest tests/integration/task_queue \
  --confcutdir=tests/integration/task_queue -q -k p1          # must fail: doubles
POC_GUARD=1 TQ_RESULTS_NAME=naive-guarded uv run --no-sync python -m pytest \
  tests/integration/task_queue --confcutdir=tests/integration/task_queue -q
```

## What each check does

| Check | File | Pass criterion |
|---|---|---|
| P1 — once per occurrence | `test_p1_p2_once_per_occurrence.py` | 2 replicas, 10-second schedule, 12 occurrences: no occurrence runs twice, none is missed |
| P2 — clock skew | same | As P1, with the skew service's clock 2 s ahead (libfaketime) and a 0.5 s job, so a plain lock is already released when the other replica fires |
| P3 — replica dies mid-job | `test_p3_replica_crash.py` | 60-second schedule, 30-second job; kill the replica running it. Records `lost`, `retried` or `resumed`. `restarted` brings the same service back after 10 s; `replaced` leaves it dead. Fails only if the occurrence completes twice or the schedule stops |
| P4 — schema through Alembic | `test_p4_schema.py` | The `migrate` service applies the candidate's schema with Alembic and exits 0; replicas then run jobs as `poc_app`, a role without CREATE, so any runtime DDL fails; every table in `expected_tables` exists |
| P6 — footprint | `test_p6_footprint.py` | Records workloads beyond the app replicas, and idle memory and CPU per service. No pass criterion |

Evidence goes to `results/<name>.json`, and container logs to
`results/logs/` (not committed).

## Adding a candidate

A candidate branch adds `candidate/` with:

- `compose.yaml` — merged over `compose.yaml` here. Paths resolve relative to
  this directory. It must define:
  - `migrate`: runs `alembic -c /harness/alembic.ini upgrade head` with
    `POC_MIGRATE_URL=postgresql://poc:poc@postgres/poc`.
  - The app replicas, each with `hostname` equal to its service name, connecting
    as `POC_DATABASE_URL=postgresql://poc_app:poc_app@postgres/poc` and passing
    through `POC_INTERVAL_SECONDS` and `POC_JOB_SECONDS`.
  - On the service named by `skew_service`: `LD_PRELOAD: ${POC_FAKETIME_PRELOAD:-}`,
    `FAKETIME: ${POC_SKEW:-+0s}` and `FAKETIME_DONT_FAKE_MONOTONIC: "0"`. Keep it `"0"`: with `"1"`,
    libfaketime makes `time.sleep` fail with `OSError: [Errno 22]`. A constant offset on
    the monotonic clock changes no intervals.
  - Any broker or extra workload the candidate needs, with pinned images.
- `Dockerfile` — `FROM tq-poc-base` (built from `harness/`), plus the
  candidate's pinned `requirements.txt`. Candidate libraries stay out of the
  repo's `pyproject.toml` and `uv.lock`.
- `migrations/` — Alembic revisions for the candidate's schema. Empty if it has
  none. Grant `poc_app` usage on any schema it creates.
- `manifest.json`:
  - `name`
  - `app_services`: the services that stand in for app replicas
  - `job_services`: services whose hostname appears in `poc_job_runs.replica`
  - `skew_service`: the service to skew in P2, or `null` for a single scheduler
  - `expected_tables`: `schema.table` names P4 checks for
- The scheduling code, calling `harness/poc_job.py`:
  - `stub_job(job, slot)` for one run.
  - `start_run` and `finish_run` as two steps, for durable workflows.
  - `claim(job, slot)` as the run-once guard.
  - `python -m poc_job` for command-per-occurrence runners.
  - Pass the framework's own scheduled time as `slot` when it provides one;
    otherwise `slot_for()` rounds to the nearest occurrence.
