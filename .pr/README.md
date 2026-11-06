# Automation cost survives sandbox cleanup

A completed automation reported $0.04 in its callback, then automatically removed
its sandbox (`keep_alive=false`). Before the fix, Enterprise retained $0.00.
With the fix, the saved cost remains $0.04 after the runtime is gone.

| Test environment | Before: callback / saved cost | Fixed: callback / saved cost |
| --- | --- | --- |
| EKS, positive usage cost with SDK candidate | $0.04 / $0.00 | $0.04 / $0.04 |
| Fresh GKE, positive cost header with stock SDK | $0.04 / $0.00 | $0.04 / $0.04 |

These live tests used normal automatic cleanup through two real LiteLLM proxies
with synthetic charges. Final totals were confirmed by fresh API reads after
runtime removal, without waiting for cost convergence before cleanup.
The tested image backported only the two changed production files onto released
Enterprise 1.79.0; it was not a full deployment of the PR's main baseline.
This does not validate a customer's router or infer unreported selection fees.

The focused suite passed 202 tests. Its lifecycle regression fails on the
baseline because the saved cost is still zero when runtime stop begins.
After the repository's unit-test setup, run:

```bash
pytest tests/unit/app_server/test_sandbox_final_metrics.py tests/unit/app_server/test_remote_sandbox_service.py tests/unit/app_server/test_sql_app_conversation_info_service.py -q
```

Coverage includes final persistence before stop, scoped accounting, timeouts,
fetch/save failures, and cleanup continuing when statistics are unavailable.
