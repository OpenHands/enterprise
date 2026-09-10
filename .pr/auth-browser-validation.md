# Local authentication: actual Enterprise app and browser validation

The local-account installation flow passed against the actual `saas_server:app`, PostgreSQL 17, Redis, the rebuilt React application, and a real SDK agent process. Authentication, CSRF middleware, admission, user management, database stores, device authorization, and sandbox ownership checks were not mocked.

## Pass matrix

| Gate | Result and evidence |
| --- | --- |
| Fresh installation without Keycloak | Actual historical schema upgraded to revision 159; mandatory default Enterprise lifespan bootstrapped exactly one administrator without Keycloak configuration, a PostHog key, or an `OH_LIFESPAN` override. |
| Browser login and restriction | Bootstrap login returned the required password-change route. Ordinary API access returned 403 `password_change_required`; cookie was host-only, Secure, HttpOnly, SameSite=Lax, path `/`. |
| Password change and admission | Browser changed the temporary password, accepted terms, completed self-hosted onboarding, and reached the application. The unverified bootstrap email could sign in without SMTP. |
| Administrator creates an account | Accounts UI returned 200 and `password_change_required:true`; ordinary personal-org owner was denied account creation with 403. [Screenshot](auth-browser/accounts-created.png). |
| Manual Git credentials | Integrations UI saved a synthetic GitHub token, and reload retained `github.com`. PostgreSQL stored an encrypted manual credential with provider account ID `10101`, null refresh/expiry fields, and no additional login identity. |
| Conversation and streamed response | UI created conversation `5daaf17b-44b9-47d5-9527-74f5a1842d34`, launched a real SDK agent, sent a chat message, and rendered the streamed synthetic model response. Changes panel also loaded. [Screenshot](auth-browser/conversation-response.png), [external boundary events](auth-browser/external-events.jsonl). |
| Device flow and API key | Real authorize → authenticated approval → token exchange succeeded for a local account. API-key settings access returned 200 with masked secrets. [Device/lifecycle results](logs/auth-browser-device-lifecycle.log). |
| SDK secret ownership | API key plus its owned RUNNING sandbox key returned 200 and the expected synthetic LLM key. Missing/invalid sandbox keys returned 401; another account with the same valid sandbox key returned 403. [Owned request](logs/auth-browser-sdk-owned.log), [final inheritance results](logs/auth-browser-sdk-inheritance.log). |
| Sandbox-scoped secrets | Actual sandbox secret-name endpoint returned 200; `github_token` retrieval returned 200 with the expected synthetic value. Missing/invalid keys returned 401 and a mismatched sandbox ID returned 403. No raw values were printed. [Results](logs/auth-browser-sdk-inheritance.log). |
| Disable and enable | Admin disable returned 200 and immediately invalidated member browser sessions and device API keys; correct-password login returned 401. Browser showed the generic incorrect-email-or-password message. Temporary re-enable allowed the cross-account SDK test; member was disabled again afterward. |
| SMTP absent | Capabilities reported `email_recovery:false`; login omitted recovery controls. A direct forgot-password request still returned the generic 200 response and issued no email. |
| SMTP verification | Real SMTPEmailService delivered a verification email to the local SMTP listener. Browser consumed the fragment token successfully and returned to settings. [Actual action requests](logs/auth-browser-email-actions.log). |
| Email replacement | Profile, login identifier, and personal-org contact remained unchanged after request. Only successful verification changed all three to the new address, set verified ownership, revoked all browser sessions, and returned to login. |
| SMTP reset | Real SMTP reset email opened the browser reset form. Reset returned 200, revoked all existing sessions, cleared the browser cookie, and returned to login without auto-login. Old password returned 401; replacement password returned 200. |
| Reset replay | Reusing the consumed email link returned 400 `detail.code=invalid_token`; UI displayed the invalid-or-expired-link message. [Actual action requests](logs/auth-browser-email-actions.log). |
| Unknown/disabled recovery | Both returned the identical generic 200 response and produced no additional SMTP delivery. |
| Operator recovery without SMTP | Actual `python -m server.auth.local.recover_admin` ran against the persisted database with interactive email and two non-echoing getpass prompts. It exited 0, retained the administrator role, revoked the browser session (subsequent request 401), and the replacement credential authenticated successfully. [Observation](logs/auth-browser-operator-recovery.log). |
| Analytics unconfigured | After the injector correction, actual SaaS restart and real login browser check returned an empty key and observed zero PostHog requests among 52 requests. [Browser result](logs/auth-browser-no-analytics.log). |
| Secret handling | App logs contained zero occurrences of the synthetic passwords, Git token, or LLM key, and no action token in access-log URLs. Captured email links used the explicit HTTPS localhost origin and fragment-only action tokens. [Scan evidence](auth-browser/secret-log-check.json). |

The final database evidence contains two users, two credentials, two personal orgs, two memberships, one instance administrator, one disabled account, one manual Git credential, consistent verified administrator email fields, and zero outstanding actions. The original bootstrap environment remained present across restarts and did not recreate the old email or reset credentials. [Database snapshot](auth-browser/final-database-evidence.json).

## Exact test boundaries

- The app used the normal Enterprise startup and lifespan. `PYTHON_DOTENV_DISABLED=1` and a small environment whitelist prevented loading personal or production configuration. No Keycloak URL, client, realm, or credential was supplied. `SESSION_API_KEY` was absent from the app environment.
- PostgreSQL was the dedicated `postgres:17` container recorded in [core state](auth-core-postgres-state.json), server version 17.10. The app used its own UUID-named database cloned from the full historical Alembic revision-157 template, then ran actual migrations 158 and 159. Redis used its own `redis:7-alpine` container and loopback port.
- [External services](auth-browser/external_services.py) implemented a local synthetic GitHub authenticated-user endpoint, an OpenAI-compatible model endpoint, and an SMTP listener. The GitHub endpoint accepted only the synthetic token. [Transport hook](auth-browser/boundary/sitecustomize.py) rewrote only outgoing `api.github.com` HTTP transport to that loopback endpoint. Actual provider parsing, credential association, encryption, refresh rules, stores, and locks ran unchanged.
- Existing direct LLM settings selected the local model endpoint and synthetic key. The actual installed OpenHands SDK 1.45.0 agent process ran; the model response was synthetic. No external LLM call was made.
- The local process runtime normally serves HTTP, while the HTTPS application upgrades runtime URLs to HTTPS/WSS. [Runtime transport](auth-browser/agent_transport.py) launched the unchanged SDK agent behind a loopback proxy that accepts HTTP and TLS and forwards raw HTTP/WebSocket traffic. Existing `CONTENT_SECURITY_POLICY` configuration added only the exact local runtime origin. The temporary certificate was trusted through `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` for the SDK's actual MCP connection back to the app. These changes were confined to the disposable harness.
- A first conversation reached database creation and actual SDK startup before the local HTTP/TLS and certificate setup was complete. Its model run failed at MCP certificate validation. The final second conversation passed browser chat and streamed-response checks with the completed transport setup; no auth or runtime implementation was substituted.
- Playwright CLI drove the real browser. External browser requests were blocked before navigation, including fonts and PostHog. This accounts for expected console network errors. An initial run found that the absent SaaS analytics key fell back to the OSS key. The integration owner corrected this; the restarted actual app then served an empty key and the real login page emitted zero PostHog requests among 52 observed browser requests. Account-action pages also correctly suppressed analytics initialization.
- SDK inheritance checks exercised the real HTTP contracts and ownership services with header credentials. They did not substitute ownership lookup with a mock. This run did not invoke a separate CloudWorkspace client wrapper or a real GitHub/LLM service.

## Commands and artifacts

The resource names and dynamically selected ports used in this run are in [browser state](auth-browser/state.json). The app was started from an empty account database; phase probes use IDs captured from that run and intentionally mutate the synthetic accounts in sequence.

```bash
cd frontend && npm run build
cd ..
python3 .pr/auth-browser/run_app.py --migrate
python3 .pr/auth-browser/external_services.py
python3 .pr/auth-browser/run_app.py
playwright-cli -s=auth-core-browser open --config=.pr/auth-browser/playwright.json
```

After completing the no-SMTP bootstrap/password/TOS/onboarding/account-creation flow, the app restarted with SMTP and provider transport enabled. Final conversation validation also used the runtime transport:

```bash
python3 .pr/auth-browser/run_app.py --provider-boundary --smtp --runtime-transport
uv run python .pr/auth-browser/check_api.py --member-already-changed
uv run python .pr/auth-browser/check_api.py --owned-sdk
python3 .pr/auth-browser/open_email.py admin@auth-test.example verify-email
python3 .pr/auth-browser/open_email.py admin-renamed@auth-test.example verify-email
python3 .pr/auth-browser/open_email.py admin-renamed@auth-test.example reset-password
python3 .pr/auth-browser/run_recovery.py
uv run python .pr/auth-browser/check_api.py --final-sdk
```

`run_recovery.py` invokes the normal single interactive recovery command using the isolated database environment; it supplies no password argument, password file, or alternate recovery mechanism. `open_email.py` opens the actual captured SMTP link and redacts its token from CLI output. SMTP captures and temporary certificate/private-key material are removed during cleanup; metadata and redacted evidence remain.

Supporting logs:

- [Frontend production build](logs/auth-browser-frontend-build.log)
- [App migrations](logs/auth-browser-migration.log)
- [Initial app/browser phase](logs/auth-browser-app.log)
- [SMTP/provider phase](logs/auth-browser-app-smtp.log)
- [Final runtime/SDK/SMTP phase](logs/auth-browser-app-runtime.log)
- [SDK process startup](logs/auth-browser-sdk-agent-startup.log)
- [Core unit and PostgreSQL validation](auth-core-validation.md)

The full local core checks passed 79 focused tests with nine opt-in PostgreSQL tests skipped; targeted Ruff and mypy checks passed. The preserved actual PostgreSQL suite passed nine tests, with the final legacy migration/downgrade case also rerun successfully. Broader shared/Keycloak/frontend checks are recorded by their owning agents.

## Cleanup

Cleanup completed: this task's named browser session, app process, SDK/proxy processes, and external test listener were stopped; its Redis container and UUID-named browser database were removed. Runtime/persistence directories, captured bearer emails, and temporary TLS keys/certificates were removed after redacted evidence was saved. Harness scripts, screenshots, metadata, and 66 checked/redacted logs and snapshots remain. [Cleanup state](auth-browser/state.json). The shared PostgreSQL container and historical template are retained until the lifecycle owner completes live Keycloak validation. No deployment, staging, commits, pushes, Helm changes, or separate automation/plugin services were performed.
