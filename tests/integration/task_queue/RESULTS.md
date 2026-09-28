# Harness self-test results

Negative control `harness/naive`: an in-process loop in each of 2 replicas. Run
on Docker 29.4.0 (OrbStack, ARM64), 2026-09-28. Raw evidence is in
`results/naive.json` (unguarded), `results/naive-guarded.json`
(`POC_GUARD=1`) and `results/naive-guarded-drain.json` (`POC_GUARD=1 POC_DRAIN=1`).

| Check | Unguarded | Guarded (`POC_GUARD=1`) |
|---|---|---|
| P1 — once per occurrence | **Fails as intended:** all 12 occurrences ran on both replicas | Pass: 12 occurrences, no doubles, none missed |
| P2 — clock skew (+2 s on replica-b) | not run | Pass. Measured offset: replica-b +2.00 s, replica-a 0.00 s |
| P3 — replica killed mid-job, restarted after 10 s | not run | `lost`. The killed run never finished; the next occurrence ran |
| P3 — replica killed mid-job, left dead | not run | `lost`. Same; the surviving replica kept the schedule |
| P4 — schema through Alembic | not run | Pass. `migrate` exited 0; replicas ran jobs as `poc_app` |
| P6 — footprint | not run | No extra workloads; about 35 MiB per replica, idle |
| P7 — rolling deploy, 10 s grace | not run | `lost`; exited on SIGTERM in 0.5 s. With `POC_DRAIN=1`: `lost`, killed at the deadline (exit 137) |
| P7 — rolling deploy, 60 s grace | not run | `lost`; exited in 1.4 s. With `POC_DRAIN=1`: **`drained`**, exited on its own after 26.6 s |

The harness therefore detects double runs, applies clock skew, tells a lost run
apart from a retried or resumed one, and tells a drained shutdown apart from a
dropped or deadline-killed one.
