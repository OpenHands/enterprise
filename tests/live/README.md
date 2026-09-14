# Local budget integration tests

This opt-in suite runs the application budget and credential controllers against
a disposable LiteLLM 1.94.1 process and real PostgreSQL. It does not mock LiteLLM's
management API, membership counters, authentication, or credential persistence.
The configured model returns a synthetic upstream response with nonzero usage;
this is not a real-provider inference test or complete product certification.

Use the supplied disposable stack. An existing licensed proxy image mirror can
be selected with `BUDGET_TEST_LITELLM_IMAGE`. Version 1.94.1 is the customer
baseline, not a certified passing release: raising a member allowance currently
fails the immediate-inference assertion because its native cache remains stale.
Run the same assertions on dependency candidates without weakening the gate.
Do not use a
shared installation. The tests reject non-local URLs, create unique native
identities, and delete the teams/users they create.

```sh
docker compose -p budget-control-local -f tests/live/litellm.compose.yaml up -d
docker compose -p budget-control-local -f tests/live/litellm.compose.yaml port proxy 4000
# Set PORT to the loopback port printed above; wait for /health/readiness.
BUDGET_TEST_LITELLM_URL=http://127.0.0.1:PORT TMPDIR=/tmp \
  uv run pytest tests/live -q --tb=short
docker compose -p budget-control-local -f tests/live/litellm.compose.yaml down
```

Teardown removes this stack's temporary PostgreSQL data, including native orphaned
budget rows. Do not reuse this project name for anything except these tests.
The default loopback port is 41400; override `BUDGET_TEST_LITELLM_PORT` if occupied.
Restart tests verify the compose labels and exact loopback binding before touching
the proxy. The database stays running across proxy restarts.

The root pytest fixture starts a separate PostgreSQL test database for the
application and migrates it to head. Missing prerequisites fail explicitly; this
suite is not silently skipped by the unit suite. CI's `tests/unit` selection does
not run these opt-in tests.

Required additional release gates include exact-image fresh install and revision
153 upgrade, the four-org/95-member reconstructed customer state, old and suspended
workers, cache/restart and failure recovery, real-provider inference, and headed
browser adoption/retry/handoff plus a working application conversation.
