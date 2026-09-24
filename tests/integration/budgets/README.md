# Organization budget integration tests

These tests run Enterprise services against migrated PostgreSQL, real LiteLLM
with its own PostgreSQL, a deterministic HTTP provider, and a management-path
fault proxy. Docker and the repository's Python dependencies are required.
Each scenario gets an isolated Enterprise database, organization and native keys.
No customer credentials or real provider charges are needed.

## Commands

```bash
uv sync --locked --all-groups
# Automatic regression group (fails CI on regressions):
uv run python -m tests.integration.budgets.run_readiness --suite regression
# Known issues (real pass/fail results; informational in PR CI):
uv run python -m tests.integration.budgets.run_readiness --suite known-issues
# All contracts, including known failures; fails on skips/xfails:
uv run python -m tests.integration.budgets.run_readiness
# Inspect the complete list without claiming a validation result:
uv run python -m tests.integration.budgets.run_readiness --collect-only
```

The full command discovers every `test_*.py` and `probe_*.py`, runs serially,
clears inherited `PYTEST_ADDOPTS` filters, and writes `.pr/budget-<suite>.xml`.
The **Budget tests** workflow runs both groups on every PR and main push.
Each job publishes a per-test summary and JUnit artifact. Known issues remain
real failures (no xfail); only their CI test step allows failure. A passing
known-issue test reports PASS, making teammates' fixes visible automatically.
Setup failures still fail the job. The manual **Budget backend readiness**
workflow runs all tests strictly, including known issues, skips and xfails.

Tests marked `@pytest.mark.budget_known_issue('OHE-…')` are informational;
all other tests, including passing probes and newly added tests, run in the
regression group. A periodic follow-up removes a marker after the test reliably
passes on main. Parametrized cases can carry individual markers. No filenames
need to change and teammates need no special commands when fixing bugs.
Do not weaken assertions or omit tests to obtain release sign-off.

**Repository setting:** an administrator must require **Budget regressions**
in the main-branch ruleset to enforce it at merge time. Do not require
**Budget known issues (informational)**. The current repository rules require
review but no status checks; adding a failing CI job alone does not enforce
branch protection. The workflow also supports merge-queue checks.

`BUDGET_LITELLM_IMAGE` selects the exact candidate image. The compatibility
default matches the Cloud chart's pinned LiteLLM 1.100.1 image.
`BUDGET_AUTH_CACHE_TTL` defaults to `0`, matching the chart's admission-cache
setting. Both the native version and this configuration are required for
immediate same-key recovery after removing an individual limit. Set the image
and TTL explicitly when reproducing an older deployment; a passing default run
does not certify that older deployment.

Admission quarantine also requires that configuration: if `/team/update` fails,
Enterprise falls back to `/team/block`, whose native implementation does not
invalidate warm authorization caches. The fault probes verify denial before the
provider, unrelated-organization isolation, unchanged spend, and recovery on the
same key after reconciliation. Both block endpoints failing remains an explicit
OHE-3268 known-issue probe; a management API fallback cannot enforce quarantine
when neither endpoint is reachable.

## Coverage

| Contract | File | Boundary |
| --- | --- | --- |
| Management auth, exact cost, partial sync/retry, legacy baseline recovery | `test_litellm_contract.py` | Service + real LiteLLM |
| Generated limit/override edits and idempotent maintenance | `test_org_budget_state_machine.py` | Service + real LiteLLM |
| Disable org cap while preserving independent member enforcement | `test_disable_organization_limit.py` | Native behavior; API outcome tested separately |
| Limit edits after spend, normal rollover and repeat maintenance | `test_budget_lifecycle.py` | Service + real spend; controlled clock |
| Actual save status/readback and unchanged-day alert save | `probe_budget_api.py` | Real FastAPI routes with injected identity/session |
| New-user provisioning and existing-user reprovisioning | `probe_membership.py` | Real `create_entries` + LiteLLM; Keycloak identity stubbed |
| Delayed rollover worker cannot renew allowance twice | `probe_rollover.py` | Two DB sessions, controlled interleaving, real spend |
| Failed Slack delivery remains retryable and then deduplicates | `probe_alert_delivery.py` | Real DB/service with simulated Slack transport |
| Reject inference after write/readback failures | `probe_fail_closed.py` | Fault proxy + provider-call counts |
| Recovery preserves spend and permits requests | `probe_recovery.py` | Fault proxy + real accounting |
| Sequential accounting and disabled member overrides | `probe_sequential_accounting.py`, `probe_disabled_override.py` | Generated service/request sequences |
| Concurrent same/cross-member admission, accounting, rejected retries | `probe_same_member_concurrency.py`, `probe_cross_member_concurrency.py` | Concurrent HTTP inference |
| Legacy key ownership, baselines, cap readback, failure/retry, task failure status | `probe_upgrade_regression.py` | Real native management and DB task runner |
| Unique provider IDs and readiness runner rejects incomplete results | `test_harness_contracts.py` | Harness regression tests |

`probe_*.py` contracts express required behavior, including known defects.
The runner explicitly collects these files in every group; markers determine
which group runs each test. A raw default pytest invocation omits probe files,
so use the runner commands above. Consult the command's current JUnit output;
this table does not label unexecuted or failing contracts as passing.

Budget alert delivery records successful Slack and individual SMTP destinations
in PostgreSQL. Failed destinations remain retryable on the next maintenance run;
successful destinations are skipped within that cycle. The native Slack probe
covers failure/recovery, and unit tests cover mixed-channel delivery across fresh
database sessions. Transport acceptance is not an exactly-once guarantee: a
crash after delivery but before commit, or an ambiguous transport timeout, can
still cause a duplicate. Controlled transports do not certify a real Slack
workspace or SMTP server.

The provisioning probe is not invitation acceptance or UI key refresh. The
HTTP fixture bypasses authentication while retaining route validation,
serialization and response status. The delayed-worker test controls a stale
read interleaving; it does not rely on probabilistic thread timing. Browser,
real-agent and release/deployment checks are tracked in
[RELEASE_READINESS.md](RELEASE_READINESS.md) and
[OHE-3322](https://linear.app/all-hands-ai/issue/OHE-3322).

## Accounting and isolation

The provider returns 10 prompt tokens and 5 completion tokens, priced at exactly
$1 per accepted request. Completion IDs remain unique across scenario resets.
LiteLLM's native batch writer runs at a shortened test interval
(`proxy_batch_write_at: 1`, plus LiteLLM jitter); tests wait for observed spend
rather than fabricating counters. Production accounting latency must be checked
separately on the release candidate. Authorization caching is disabled to match the deployment contract; LLM response
caching is unaffected.

The provider binds the host interface so Linux containers can reach it through
`host.docker.internal`; the fault proxy stays on loopback. Use an isolated test
host. Resources are torn down after the session. The legacy fixture deliberately
starts unreconciled: writing its override must not synchronize away the missing
baselines before the test runs. The ownership and task-status tests exercise
current implementations instead of obsolete xfail placeholders.

## Generated and abstract checks

```bash
BUDGET_STATE_MACHINE_EXAMPLES=30 BUDGET_STATE_MACHINE_STEPS=20 \
  uv run pytest tests/integration/budgets/test_org_budget_state_machine.py -n 0
npx --yes @informalsystems/quint@0.29.1 typecheck quint-specs/org-budget.qnt
npx --yes @informalsystems/quint@0.29.1 run \
  --invariant=allProperties --max-steps=25 --max-samples=10000 --backend=rust \
  quint-specs/org-budget.qnt
```

The healthy generated campaign defaults to 8 examples of 10 transitions. Request
and disabled-override transitions run in the explicit safety probes. Quint
explores the abstract contract; it does not prove Python, API, cache or provider
behavior. In particular, delayed/out-of-order policy-version verification is
not fully covered by this concrete harness and remains a coordination-work
follow-up in OHE-3259. Backend gate success must not be presented as complete
customer release sign-off.
