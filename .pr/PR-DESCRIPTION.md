<!-- Keep this PR as draft until it is ready for review. -->

HUMAN:

- [ ] A human has tested these changes.

AGENT:

---

## Why

A default prompt automation can finish with a $0.04 callback, immediately delete its sandbox, and leave Enterprise's conversation cost at $0 permanently. We reproduced this on both an existing EKS lab and a fresh GKE Helm install running Enterprise 1.79.0. The completion callback and the app save costs separately. Cleanup can remove the runtime before the app has saved the final amount.

Before stopping a running runtime, this change fetches and saves its conversation statistics through the existing accounting code. If the fetch or save fails, cleanup still proceeds after the bounded attempt. An unhealthy runtime must remain stoppable.

## Summary

- Snapshot each accessible conversation's live statistics before stopping a running remote sandbox, using the existing scoped, idempotent statistics writer.
- Bound the whole snapshot pass to ten seconds; log fetch/commit failures and continue shutdown, preserving existing totals and the ability to stop unhealthy or runaway runtimes.
- Commit session-key revocation before network work. Preserve deletion of paused, stopped, missing and errored runtimes and preserve stored totals when a conversation is already gone.

## Issue Number

Related to OHE-3895. This addresses a separate Enterprise persistence failure found during end-to-end qualification of the SDK usage.cost fix. No automation or SDK dependency changes are required for this persistence fix. With the stock SDK this preserves costs the SDK already computes, such as header-priced calls; usage-only aliases also need the SDK fix.

## How to Test

Run the repository's unit-test setup, then:

```bash
pytest tests/unit/app_server/test_sandbox_final_metrics.py tests/unit/app_server/test_remote_sandbox_service.py tests/unit/app_server/test_sql_app_conversation_info_service.py -q
pre-commit run --config dev_config/python/.pre-commit-config.yaml --files openhands/app_server/sandbox/final_metrics.py openhands/app_server/sandbox/remote_sandbox_service.py tests/unit/app_server/test_sandbox_final_metrics.py tests/unit/app_server/test_remote_sandbox_service.py
```

202 tests pass. Required commit hooks, including mypy, pass. The new lifecycle regression fails on the baseline because the persisted cost is still zero when /stop is called. Coverage includes multiple usage buckets, scoped sandbox lookup, caller identity, no helper DB connection across HTTP, 404 preservation, fetch/commit failures, a total deadline, unavailable runtime cleanup and upfront key revocation before network I/O.

Final-image validation includes two before/after pairs plus two billing-contract controls, all using normal service cleanup: EKS usage-only $0.04, aggregate usage $0.05, explicit-zero-header $0, and GKE positive header $0.04. All matched expectations after runtime removal, verified by eight fresh API reads. Before: callback $0.04 / saved app $0. After: $0.04 / $0.04, on both EKS and fresh GKE. [Before/after and correlated records](.pr/README.md) include IDs, synthetic ledgers and limitations. The test image backports only the two changed production files onto the exact released Enterprise 1.79.0 digest, preserving each cluster's chart, authentication, runtime SDK and model configuration.

## Video/Screenshots

[Before/after API evidence](.pr/README.md#live-before--after) shows durable cost after runtime removal. There is no UI layout change; these records do not claim that Canvas can reconnect to a removed runtime.

## Type

- [x] Bug fix
- [ ] Feature
- [ ] Refactor
- [ ] Breaking change
- [ ] Docs / chore

## Notes

No migration or public API change. This cannot recover spend after the runtime has already disappeared, infer unreported router-selection fees, or change the SDK's authoritative-header precedence. A snapshot failure is logged and teardown proceeds; the prior saved cost may remain stale when the agent server cannot provide its final statistics. Runtime stop/lookup failures retain their existing 503 behavior. Conversation deletion, pause and runtime-provider reaping are outside this sandbox-cleanup fix. Tests use synthetic billing amounts, not customer inference.
