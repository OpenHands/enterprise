# Daily sandbox quota — feature-preview test evidence

Preview: https://pr-1486.staging.all-hands.dev (saas-deploy#1486, namespace `saas-deploy-pr-1486`)
Images under test: `ghcr.io/openhands/enterprise-server:sha-7d904eb` (the code head of enterprise#786 when tested; this docs commit is the only later commit), `ghcr.io/openhands/automation:sha-78c512a` (automation#583 head)
Test account: the preview's only user; org limit driven down to **3** in the preview DB
(`UPDATE org SET daily_conversation_limit=3` — the column since renamed to `daily_sandbox_limit` by migration 189)
and the day's usage reset, so the sequence below starts from zero.

## 1. Quota starts at 0 of 3

```
$ GET /api/quota/status
{"daily_limit":3,"used_today":0,"remaining":3,"reset_at":"2026-10-15T00:00:00+00:00", ...}
```

## 2. Automation created and dispatched; each run consumes a sandbox

```
$ POST /api/automation/v1/preset/prompt   {"name":"Quota GIF test", ...}
201 {"id":"7e72ed3b-9278-4532-9e40-c291f8a0bd82", ...}

$ POST /api/automation/v1/7e72ed3b-.../dispatch   (x4, spaced across two batches)
201 PENDING ...
```

Quota API after each sandbox reached its runtime (counting happens at
sandbox creation in `RemoteSandboxService.start_sandbox`, before the run's
conversation executes):

```
after run 1: {"daily_limit":3,"used_today":1,"remaining":2, ...}
after run 3: {"daily_limit":3,"used_today":3,"remaining":0, ...}
```

## 3. The 4th run is SKIPPED with the new status detail (automation#583)

```
$ GET /api/automation/v1/7e72ed3b-.../runs?limit=6
status_counts: {'FAILED': 3, 'SKIPPED': 1}

2026-10-14T21:04:47 FAILED  | execution_error      | Conversation run failed ... 403 team_model_access_denied (see note)
2026-10-14T21:08:03 FAILED  | execution_error      | Conversation run failed ... 403 team_model_access_denied (see note)
2026-10-14T21:08:07 SKIPPED | daily_sandbox_limit  | Daily sandbox limit of 3 reached. Request a quota increase at /settings/quota or ...
2026-10-14T21:08:10 FAILED  | execution_error      | Conversation run failed ... 403 team_model_access_denied (see note)
```

The SKIPPED run was dispatched while the quota was at 3/3: sandbox creation
returned 429 `DAILY_SANDBOX_LIMIT_REACHED`, the automation backend raised
`DailySandboxLimitReachedError` without retrying, and the dispatcher marked
the run SKIPPED (transient, source `sandbox_api`) instead of FAILED.

DB cross-check (preview Postgres, `daily_sandbox_usage` — the new table):

```
user_id                              | usage_date | sandbox_count
65878736-f2bf-45b0-9639-384eda217d55 | 2026-10-14 | 3
```

## 4. Direct sandbox creation is rejected with the structured 429

```
$ POST /api/v1/sandboxes
429
{"detail":{"code":"daily_sandbox_limit_reached","error":"DAILY_SANDBOX_LIMIT_REACHED",
 "message":"Daily sandbox limit of 3 reached. Request a quota increase at /settings/quota or https://u8mk1.share.hsforms.com/...",
 "limit":3,"used":3,"reset_at":"2026-10-15T00:00:00+00:00"}}
```

## 5. A GUI conversation start fails through the same sandbox gate

```
$ POST /api/v1/app-conversations {"trigger":"gui"}
200 {"id":"6fa47fe4fb554898a590621a8ec3f9ee","status":"WORKING", ...}

$ GET /api/v1/app-conversations/start-tasks/search
{"id":"6fa47fe4fb554898a590621a8ec3f9ee","status":"ERROR",
 "detail":"429: {'code': 'daily_sandbox_limit_reached', 'error': 'DAILY_SANDBOX_LIMIT_REACHED', ... 'limit': 3, 'used': 3, ...}",
 "sandbox_id":null, ...}
```

Note: usage after these two rejected attempts is still 3 — rejected starts do
not consume quota (nothing to release: no sandbox was created).

## Note on the FAILED runs

The 3 non-skipped runs FAILED with `team_model_access_denied` from this
preview's LiteLLM team (`deepseek-v4.1-flash` not on its allowlist — the preview team can only access `deepseek-v4-flash`). This is a preview-environment model-allowlist quirk,
unrelated to this change; importantly, the runs still demonstrate the quota
semantics: the sandbox (and its daily-quota slot) is allocated *before* the
conversation's LLM call, which is exactly the resource this quota protects.

## Also fixed along the way (saas-deploy#1486)

Getting automations to run on *any* feature preview required fixing the
preview object storage, broken before this change:

1. `minio.enabled` was never set (chart default false) although the feature
   values configured MinIO and pointed automation at `openhands-minio:9000`
   → automation creation 500 `NoCredentialsError`.
2. MinIO's quay.io images now 401 for anonymous pulls → previews use the
   chart's bundled RustFS (S3, same default creds) served as
   `openhands-minio-svc:9000`.
3. The automation release's `filestore` values set only type/bucket — the
   `_env` template renders endpoint/creds from `filestore.endpoint/
   accessKey/secretKey`, which were missing → same `NoCredentialsError`.
4. RustFS PVCs needed `hyperdisk-balanced` at the 4Gi minimum (C4 nodes
   can't attach pd-balanced).

Each is `fix(feature): ...` commits on saas-deploy#1486, verified live:
`openhands-minio-*` 1/1 Running, automation creation 201, runs dispatching.
