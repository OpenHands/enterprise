# Budgets release readiness

Tracking: [OHE-3322](https://linear.app/all-hands-ai/issue/OHE-3322).
A completed harness PR is not a customer-ready release. Evaluate one exact
Enterprise, LiteLLM and chart candidate, keeping its results and unresolved
failures together. Customer-specific 1finity rollout remains separate.

## Before sign-off

- Agree that disabling the organization limit preserves individual overrides;
  explicitly decide whether inherited defaults also remain active.
- Fix confirmed defects with regression tests: allowance reset on unchanged-day
  saves (OHE-3318), failed disable/retry (OHE-3319), misleading default copy
  (OHE-3320), failed Slack delivery retries (OHE-3321), and unavailable Slack
  setup controls (OHE-3204), new-user default enforcement (OHE-3333), and
  removal of disabled individual caps (OHE-3334).
- Verify enforcement failure/recovery (OHE-3268 / PR359), and coordinated
  reconciliation/rollover (OHE-3259 / PR403). Review the final implementation
  chosen from overlapping PRs rather than assuming all branches must merge.
  PR359's emergency team block requires uncached native authorization and an
  available `/team/block` endpoint. Denial when both block endpoints fail remains
  an informational OHE-3268 probe, not a guarantee provided by the fallback.
- Run the full backend gate below, including fault and concurrency probes.
  No skipped or expected-failure contract counts as passing readiness.
- Run separate browser/API/actual-agent E2E tests: change settings, use a real
  conversation, reach both limits, observe actionable errors, raise a limit
  and resume. Check user isolation and requests already in flight.
- Exercise invitation acceptance and actual managed-key refresh entrypoints.
  Native key/provisioning probes here do not replace authentication/UI tests.
- Verify deployed maintenance scheduling, normal and delayed month rollover,
  overlapping workers, and recovery from a LiteLLM outage.
- Verify alert setup validation, delivery, retry and deduplication. The Slack
  fault-injection test here does not establish a real workspace connection.
- Record exact versions, commands, failures and evidence. Resolve each failure
  or record an explicit product exception before signing off. A green default
  test suite, a merged PR, or a Done ticket is not release-level proof.

## Backend test PR versus follow-ups

PR360 owns the reusable real-service harness, backend lifecycle/fault probes,
HTTP budget response coverage and this explicit readiness command. Product
fixes stay in focused PRs. Both regression and informational known-issue groups
run automatically on PRs and main; periodically remove a `budget_known_issue`
marker after its test reliably passes on main. A separate E2E PR owns browser
and actual-agent flows. Do not
replace this harness with a second backend environment.

```bash
uv run python -m tests.integration.budgets.run_readiness
```

This runs every `test_*.py` and `probe_*.py` serially and writes
`.pr/budget-all.xml`. Any failure, skip or xfail prevents success.
Collection alone is available with `--collect-only` and is not validation.
Set `BUDGET_LITELLM_IMAGE` to the exact release image under evaluation; the
default matches the Cloud chart’s digest-pinned 1.100.1 image and authorization
cache TTL 0. Set `BUDGET_AUTH_CACHE_TTL` explicitly when testing a different
deployment configuration. The automatic regression command is:

```bash
uv run python -m tests.integration.budgets.run_readiness --suite regression
```

The provider is deterministic real HTTP; Enterprise and LiteLLM have separate
real Postgres databases. Budget HTTP tests use real route validation and
serialization with injected test identity/session, not production login.
Alert transport failures and rollover clock/interleaving are controlled test
inputs. No test should require customer credentials or send real Slack/email.
