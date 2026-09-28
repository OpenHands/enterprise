# Supercronic results

Supercronic v0.2.49, the latest release, running as PID 1 in one `cron`
container. It runs `python -m poc_job tick` once per occurrence. The two app
replicas are plain FastAPI apps that schedule nothing. Run on Docker with
OrbStack on ARM64, 2026-09-28, while other candidates ran on the same host.
The raw evidence is in `results/supercronic.json` and
`results/supercronic-two-replicas.json`.

| Check | `supercronic` (1 cron container) | `supercronic-two-replicas` (`cron-a` + `cron-b`) |
|---|---|---|
| P1: once per occurrence | Pass. 12 occurrences, no doubles, none missed | **Fails, as expected.** All 12 occurrences ran twice, once on each cron container |
| P2: clock skew | Skipped (single scheduler, `skew_service: null`) | not run |
| P3: killed mid-job, restarted after 10 s | Pass, outcome `lost`. The killed run never finished and nothing retried it. The restarted container ran the next occurrence | not run |
| P3: killed mid-job, left dead | **Fail**, outcome `lost`, `next_occurrence_ran: false`. With the only scheduler dead, the schedule stops | not run |
| P4: schema through Alembic | Pass. `migrate` exited 0, there is no candidate schema, and jobs ran as `poc_app` | not run |
| P6: footprint | 1 extra workload (`cron`), 8.1 MiB and 0% CPU when idle. Replicas use about 28 MiB each | not run |

## Setup

- `Dockerfile`: `FROM tq-poc-base`. It uses `ADD` to download
  `supercronic-linux-${TARGETARCH}` from the release, then checks it with
  `sha1sum -c` against the SHA1 published in the v0.2.49 release notes. Only
  amd64 and arm64 have a pinned checksum; any other architecture fails the
  build. The release publishes SHA1 only, not SHA256, so `ADD --checksum`
  (which takes SHA256) can't be used. The base image has no curl, so `ADD`
  does the download.
- `cron.sh`: builds `/tmp/crontab` from `POC_INTERVAL_SECONDS` when the
  container starts, then runs `exec supercronic /tmp/crontab`.
- Inputs are the crontab and the job's environment. Supercronic passes its own
  environment through to jobs, so `POC_DATABASE_URL` and the other settings
  reach the job with no extra config.
- There are no migrations, tables or broker. Supercronic keeps no state.
- The real jobs would run the same way, as crontab lines such as
  `0 * * * * python -m run_maintenance_tasks`, with no code change. The
  entrypoints stay as they are today, and only the CronJob schedule moves into
  a crontab.

## Gotchas

- **Seconds need all seven fields.** Supercronic's `cronexpr` parser treats
  five fields as minute-level. With six fields, the sixth is the *year*, not
  seconds. To get seconds you need seven fields, seconds first:
  `*/10 * * * * * *`. The log confirmed it: `job.schedule="*/10 * * * * * *"`,
  firing at :00, :10, :20.
- **Build contexts with `extends`.** When the variant used `extends` from
  `candidate/compose.yaml`, Compose resolved `build: candidate` relative to the
  extended file, giving `candidate/candidate`. The variant's compose file is a
  plain copy with `cron-a` and `cron-b` instead.
- **Cold start.** Each run is a fresh Python process that takes about 0.1 to
  1.2 s to start. This adds a little to `fired_at`, but `slot_for()` rounding
  absorbs it.

## What P3 showed

Supercronic runs each job as a child process and does not track it anywhere
else. When the container is killed with SIGKILL, the run in progress dies with
it. That occurrence is **lost**: it is never retried and never marked as
failed. A restarted container simply waits for the next occurrence.

The "replaced" case fails because this candidate has a single scheduler. When
nothing brings that scheduler back, nothing runs. In Kubernetes, a
`replicas: 1` Deployment would start a new pod, so the next occurrence would
run there. The harness models "replaced" as "left dead", which is fair for
multi-replica candidates but guarantees a failure for a single-scheduler one.
The finding still holds: availability depends entirely on the orchestrator
restarting the one pod. While it is down, any occurrences that fall in that
window are skipped, not caught up.

## Desk-research claims

- **No coordination between replicas: confirmed.** Two containers ran every
  occurrence twice (P1 on the variant). You must run exactly one, for example
  a Deployment with `replicas: 1` and `strategy: Recreate`. A
  `RollingUpdate` briefly runs two pods and can double-run an occurrence.
- **Skips a job while its previous run is still going: confirmed.** A manual
  run used a 25 s job on a 10 s schedule. The log showed `not starting: job is
  still running since ... (10s elapsed)` and then `(20s elapsed)`, followed by
  `job took too long to run`. The skipped occurrences are dropped, not queued.
  This test did not try `-overlapping`.
- **Waits for running jobs on SIGTERM: confirmed, within the grace period.**
  During a 25 s job, `docker compose stop -t 60 cron` logged `waiting for jobs
  to finish`. The job completed and wrote `finished_at`, then Supercronic
  logged `exiting`. The wait only lasts as long as the orchestrator's grace
  period allows. That is 10 s by default in Compose (`stop_grace_period`) and
  30 s in Kubernetes (`terminationGracePeriodSeconds`). After that the job is
  SIGKILLed and lost, as in P3. For long maintenance jobs, set the grace
  period above the longest job.
- Not in the desk research: when Supercronic runs as PID 1 it also reaps zombie
  processes (the log says `reaping dead processes`), so no init wrapper is
  needed.
