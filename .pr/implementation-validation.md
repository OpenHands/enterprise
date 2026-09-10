# Enterprise authentication implementation validation

Validated September 10, 2026. The [technical plan](keycloak-removal-technical-plan.md) and [handoff](keycloak-removal-handoff.md) remain preserved. The implementation is committed locally on `jlaverty/keycloak-optional-auth`, rebased onto `origin/main` at `21b0ca4b2`. The implementation-phase results below were collected before the rebase; final branch checks are recorded separately. No push, deployment, or Helm change was performed.

## Final branch checks after rebase

Implementation commit: `280113316`. The branch includes the six upstream commits through `21b0ca4b2`, including SDK 1.46.0, release 1.59.1, self-hosted telemetry policy, and the shared PostgreSQL test harness. The [installed dependency versions](auth-post-rebase-dependencies.json) confirm SDK, agent-server, and tools 1.46.0. Repository-pinned uv 0.11.32 passed `uv lock --check` without modifying the merged lockfile.

| Check | Result | Evidence |
| --- | --- | --- |
| Combined backend authentication, account, integration, storage, SDK settings, and context tests, 53 files | 1,378 passed, 27 skipped | [pytest output](logs/auth-post-rebase-unit.log), [test manifest](auth-post-rebase-unit-files.txt) |
| Required staged Python pre-commit over the complete 193-file implementation, including 139 backend files and whole-backend mypy | Passed | [hook output](logs/auth-post-rebase-precommit.log), [backend manifest](auth-post-rebase-precommit-files.txt), [implementation manifest](auth-implementation-files.txt) |
| Frontend authentication/settings/analytics tests, 11 files | 144 passed, one skipped | [Vitest output](logs/auth-post-rebase-frontend-tests.log), [test manifest](auth-post-rebase-frontend-files.txt) |
| Frontend `npm run lint:fix`, `npm run build`, `npm run typecheck` | Passed | [lint](logs/auth-post-rebase-frontend-lint.log), [build](logs/auth-post-rebase-frontend-build.log), [types](logs/auth-post-rebase-frontend-types.log) |

Backend invocation: `PYTHON_DOTENV_DISABLED=1 uv run --frozen --group test pytest -q --maxfail=5` with the linked manifest. The upstream harness used a disposable PostgreSQL 16 container and fully migrated databases, then removed its resources. The four newly added SQLite schema fixtures retain SQLite and create only the authentication/account tables they exercise, excluding unrelated PostgreSQL-only integration columns. Permanent opt-in PostgreSQL concurrency cases remain skipped in this combined run; their earlier real PostgreSQL evidence is preserved below.

Rebase checks preserve both upstream self-hosted telemetry suppression and suppression on account-action pages carrying token fragments. Existing API-key and provider fixtures now seed their required organization/account rows under PostgreSQL constraints. The direct account-cleanup regression also found and verified a same-transaction ORM flush before raw SQL deletion, preventing a stale update after identity deletion. These final results include all affected fixtures and the cleanup regression.

The preserved plans, reports, harnesses, and redacted evidence also passed [staged artifact hooks](logs/auth-artifacts-precommit-final.log). Raw browser snapshots, duplicate logs, and runtime caches remain local and ignored.

The earlier live browser/conversation evidence used SDK 1.45.0 as recorded in its report. The post-rebase SDK 1.46.0 evidence is the combined regression run above; the live browser and Keycloak runs were not repeated after the rebase.

## Implementation-phase checks

| Check | Result | Evidence |
| --- | --- | --- |
| Changed backend tests plus SDK sandbox-secret/CORS regressions, 47 files | 1,157 passed, 27 skipped | [pytest output](logs/auth-integration-unit-final.log), [test manifest](auth-unit-files.txt) |
| Existing SDK settings and user/organization context, four additional files | 52 passed | [pytest output](logs/auth-sdk-context-regressions.log) |
| Additional LiteLLM and organization membership storage regressions | 169 passed | [pytest output](account-additional-storage-tests.log) |
| Required Python pre-commit over 138 changed/new backend files, including whole-backend mypy | Passed | [hook output](logs/auth-integration-precommit-final.log), [file manifest](auth-precommit-files.txt) |
| Frontend authentication/settings tests, 11 files | 143 passed, one skipped | [Vitest output](logs/auth-integration-frontend-tests-final.log) |
| Frontend `npm run lint:fix`, `npm run build`, `npm run typecheck` | Passed | [lint](logs/auth-integration-frontend-lint-final.log), [build](logs/auth-integration-frontend-build-final.log), [types](logs/auth-integration-frontend-types-final.log) |
| Final logout, upstream outage, and analytics regressions | 255 backend tests and eight frontend analytics tests passed | [backend output](logs/auth-logout-analytics-regressions.log), [frontend output](logs/auth-posthog-frontend-regression.log) |
| PostgreSQL legacy callback and concurrent Slack linking | Two passed | [PostgreSQL output](logs/auth-integration-postgres-final.log), [preserved tests](../tests/unit/server/auth/test_legacy_callback_postgres.py) |

The backend skips include opt-in PostgreSQL cases and preexisting database-specific skips; PostgreSQL evidence is separate below. This host runs Node 25, whose experimental WebStorage shadows jsdom's localStorage. Frontend tests passed with `NODE_OPTIONS=--no-experimental-webstorage`; no application workaround was added. The required mypy hook used its own `.pr/auth-precommit-mypy-cache` to avoid sharing a cache between different mypy versions.

The implementation-phase backend manifest covered 138 changed/new backend files, including the logout and web-client injector additions, with no `.pr` entries. Its `git diff --check` passed. The final branch manifests additionally record the SQLite schema helper added during rebase validation.

## Security and compatibility evidence

- [Shared authentication tests](../tests/unit/server/auth/test_shared_authentication.py) cover canonical UUIDs, disabled accounts, local requests with Keycloak constructors forbidden, API-key header precedence and organization binding, actual cookie-based CSRF, and invalid-header fallback. Exposed SaaS settings require a header-authenticated API principal plus an owned running sandbox; browser sessions and API-key cookies cannot obtain raw settings even with sandbox proof.
- Initial-password browser sessions are denied ordinary API access, API-key creation, and device approval before changing the password. An API key explicitly issued by an authorized administrator is a separate credential and remains usable for existing SDK/service provisioning. API-key cookies still require CSRF and never gain header-only secret exposure privileges.
- [Local core evidence](auth-core-validation.md) records nine real PostgreSQL cases for bootstrap concurrency/rollback, password/reset races, email proof, invitation enrollment, and migration preservation. [Lifecycle PostgreSQL tests](../tests/unit/server/services/test_account_lifecycle_postgres.py) cover thirteen cumulative cases, including administrator invariants, cleanup ordering, workspace reset, and legacy hydration; [account service tests](../tests/unit/server/services/test_enterprise_user_management.py) cover local provisioning and permissions.
- The actual legacy callback regression starts with a settings-only account and preserves its UUID, personal organization/membership, API-key ID/hash, language, custom model/base URL, and encrypted LLM credential. Fresh-signup creation is forbidden in that test. Trusted background jobs and proven broker actor/token paths explicitly hydrate legacy accounts through the Keycloak boundary.
- [Slack tests](../tests/unit/server/auth/test_slack_local_auth.py) cover message-generated links entering the session-bound install route, nonce/session/account mismatch rejection, disabled-account checks, collision rejection, and inactive Keycloak callbacks in local mode. The PostgreSQL race allows one successful first link and rejects the competing owner with 409.

- [Upstream outage regressions](../tests/unit/server/auth/test_keycloak_outages.py) exercise real SDK exception types for HTTP 408, 429, and server errors, including nested retry wrappers. Token exchange, userinfo, refresh, offline validation/introspection, and duplicate-policy lookup preserve temporary unavailability. Signed-cookie middleware returns 503 without replacing/deleting the cookie or changing mode. Genuine invalid-grant/401/403 behavior remains compatible. The affected authentication suite passed 279 tests; [focused output](logs/auth-keycloak-outage-regressions.log).
- [Logout regressions](../tests/unit/server/auth/test_logout_sessions.py) run the actual shared route and middleware with expired signed cookies, including multiple chunks, HTTP 429/503, and connection failures. Logout clears the browser credentials while retaining CSRF/origin enforcement and API-key independence. Middleware respects session cookies explicitly written or deleted by routes. The disposable Keycloak run also verified both expired-cookie logout and logout during an outage.
- Unconfigured SaaS serves an empty analytics key; OSS retains its existing default and explicit keys remain supported in both modes. The actual SaaS login browser observed zero PostHog requests after a restart without the key. [Injector regressions](../tests/unit/app_server/test_default_web_client_config_injector.py) cover unset, empty, whitespace, and explicit configuration.

## Application and external validation

- [Local application and browser validation](auth-browser-validation.md) passed bootstrap, password change, terms/onboarding, administrator account creation, manual repository tokens, a conversation with a real SDK agent, device authorization, sandbox-secret ownership, disable/enable, SMTP verification/replacement/reset/replay, and interactive operator recovery without SMTP. It used the actual Enterprise app and PostgreSQL stores; GitHub and model HTTP responses were synthetic local services. No-key analytics was rechecked against the restarted app.
- [Live Keycloak compatibility validation](auth-keycloak-validation.md) passed actual authorization-code exchange, legacy hydration with stable IDs/settings, cookie refresh/logout, existing API keys, and real OIDC and signed SAML broker flows. Controlled HTTP 503/429 and stopped-server faults preserved the cookie and Keycloak mode; the same cookie worked after recovery. Both expired-cookie logout and logout during the outage passed. The provider was a disposable loopback Keycloak instance, not a customer directory or production deployment. [Machine-readable results](auth-keycloak/results.json) preserve all 18 result groups; [cleanup evidence](auth-keycloak/cleanup.json) records removal of the disposable resources.
