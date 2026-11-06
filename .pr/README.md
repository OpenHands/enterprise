# Preserve automation cost before remote runtime cleanup

The default prompt preset calls `DELETE /api/v1/sandboxes/{id}` immediately after its completion callback. Enterprise's asynchronous statistics refresh can lose that race. The callback records $0.04, but the app conversation remains at $0 after its runtime disappears.

This focused fix takes a bounded, best-effort snapshot of the running runtime's conversation statistics before `/stop`, using the existing scoped statistics writer. Failures are logged and shutdown continues. It does not copy a run-wide scalar into each conversation or invent a charge.

## Live before / after

| Environment and cost source | Before: callback / saved app | Final candidate: callback / saved app | Runtime after cleanup |
| --- | --- | --- | --- |
| EKS, positive usage only, candidate SDK 1.60.1 | $0.04 / $0 | $0.04 / $0.04 | MISSING |
| Fresh GKE, positive header, stock SDK 1.58.1 | $0.04 / $0 | $0.04 / $0.04 | MISSING |
| EKS, aggregate usage including synthetic selection | — | $0.05 / $0.05 | MISSING |
| EKS, explicit zero header with positive usage | — | $0 / $0 | MISSING |

The last two rows match the expected SDK billing contract; the zero result alone cannot distinguish this fix from the bug. They do not prove an actual router-selection implementation or solve conflicting zero headers. All amounts and generation/selection stages are synthetic; the requests traverse two real LiteLLM proxies, including the application's bundled gateway. No customer upstream service or paid classifier was contacted.

All four final-image automations completed with `keep_alive=false`. Eight fresh API reads (two per conversation) confirmed their final totals after runtime removal. The harness did not wait for cost convergence before cleanup. Saved profile `stream=false` still results in streaming agent inference because Enterprise forces it on; it is not non-streaming application coverage.

The test image is a **two-file backport onto released Enterprise 1.79.0**, not a full upgrade to the PR's main baseline. Source head: `c364334bd93f6d847d4b4aa6a7b873598b4ec010`; tested digest: `sha256:8b3818124d83706cfe3a30412ef96049f977a108947e2d66e724c5560663ab33`. Both clusters used that digest, and non-image Helm values were unchanged. GKE retained its stock SDK; EKS used the separately tested SDK candidate.

## Evidence and reproduction

- [Baseline app records](evidence/baseline-application.json), including exact conversation/run/backend IDs.
- [Final app records, ledger and fresh reads](evidence/candidate-application.json).
- [Deployed images and Helm preservation](evidence/deployment-verification.json).
- [202 passing tests](evidence/candidate-tests.txt), [behavioral baseline failure](evidence/baseline-regression.txt), [required checks](evidence/required-checks.txt).
- [Exact source review](evidence/source-review-response.json): local Opus 5.5, APPROVED; model review under the author's Canvas, not human or GitHub approval. Earlier blocking lifecycle findings were fixed before the final image.
- [Released automation cleanup route](evidence/automation-1.25.1-cleanup.py.txt) confirms the sandbox DELETE path. Conversation soft-delete is a separate path outside this change.
- [Test image preparation](image/README.md), with pinned base and recorded source hashes.

After the repository's documented unit-test environment setup, the captured credential-free runner can reproduce the focused suite:

```bash
python .pr/evidence/unit-runner.py.txt tests/unit/app_server/test_sandbox_final_metrics.py tests/unit/app_server/test_remote_sandbox_service.py tests/unit/app_server/test_sql_app_conversation_info_service.py -q
```

Captured text logs have trailing whitespace normalized by the repository hooks. The patch is JSON-encoded to preserve diff context exactly.

The new lifecycle regression fails on the unmodified baseline because saved cost is zero when stop executes. Helper tests mock the injectors/writer and exercise real httpx requests via MockTransport; surrounding lifecycle tests include real PostgreSQL key-revocation/rollback coverage. The live tests exercise actual Enterprise storage and APIs on both clusters.

To reproduce the application bug on an authorized test install, route an unpriced alias through the bundled and upstream LiteLLM with a deterministic positive final usage amount or positive cost header. Dispatch a manual prompt automation with `keep_alive=false`. Compare the completed run callback with fresh app-conversation metrics after `sandbox_status=MISSING`. In these baseline runs the app froze at zero; the candidate retained the known amount. Keep callback amounts, per-conversation totals and auxiliary calls separate.

## Scope and retained state

No migration, public API or dependency update. Only the remote backend's explicit sandbox teardown changes. Conversation deletion, pause, provider reaping and other sandbox backends are outside scope. If the agent server cannot provide metrics, cleanup still succeeds and the last saved cost can remain stale. Existing runtime lookup/stop failures keep their retryable behavior.

All 41 retained visible EKS task definitions and five GKE task definitions are disabled. One earlier rejected-attempt definition is no longer returned in the approved EKS org; the audit records it separately rather than inferring cross-org state. Completed run/conversation evidence, isolated proxies and test images remain for review. Existing auth, orgs, model defaults, credentials and teammate deployments were preserved. Private rollback values remain local; no credentials are packaged here.

Journal legend: `app-baseline` / `app-candidate` identify the SDK variant; `server-reviewed` identifies the Enterprise candidate. [Baseline versions/revisions and canonical non-image settings hashes](evidence/settings-preservation-hashes.json) supplement the image records. Baseline pod imageIDs were not captured; no digest is inferred from the version tag.

[Publication source equivalence](source-equivalence.json) records the unchanged reviewed production source and the clean publication branch. Original artifact/review-head IDs remain local provenance; do not push the original research branch.
