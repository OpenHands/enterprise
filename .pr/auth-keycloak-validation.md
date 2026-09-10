# Live Keycloak compatibility validation

Status: all listed live checks passed on 2026-09-10.

Tested the actual `saas_server:app` with its normal lifespan and authentication code, a disposable PostgreSQL 17 database, separate Redis, and Keycloak 26.5.5. All services bind to loopback. The harness uses generated synthetic credentials and test accounts. No product authentication code was mocked. Setup and browser automation live in [auth-keycloak](auth-keycloak/), with concise [machine-readable results](auth-keycloak/results.json).

| Check | Evidence |
| --- | --- |
| Legacy upgrade | Seeded settings-only account and existing API key at Alembic 157; ran real 158 and 159 migrations. Startup selected and persisted `keycloak` with no `KEYCLOAK_ENABLED` override. |
| Canonical hydration | Real browser authorization-code callback created the canonical graph while preserving the user UUID, same-ID personal organization, settings row, API key ID/value, language, model, base URL, and encrypted LLM key. No local credential was created. |
| Browser login | Keycloak login form → real authorization code exchange → Enterprise callback 302 → Secure, HttpOnly cookie → authenticated `/api/v1/users/me` 200. |
| Token refresh | With a 10-second access-token lifespan, an expired access token refreshed against the running Keycloak server; the cookie rotated and `/me` returned 200. |
| API key continuity | The pre-upgrade API key authenticated after login and refresh, and after ordinary browser logout. |
| Ordinary logout | Logout returned 200, removed browser authentication, and subsequent browser `/me` returned 401 while the existing API key still returned 200. |
| OIDC Enterprise SSO | A second synthetic Keycloak realm acted as the OIDC provider for the single `enterprise_sso` alias. Real broker login and Enterprise callback succeeded, including the compatibility offline-token callback. The verified user had a same-ID personal organization and `/me` returned 200. |
| SAML Enterprise SSO | Reconfigured the same single alias to use the upstream realm as a SAML IdP, with RSA signatures and signature validation enabled. The browser sent real SAMLRequest and SAMLResponse POSTs; Enterprise callback returned 302 and authenticated `/me` returned 200. The profile carried `enterprise_sso:saml`. |
| Credential separation | Both SSO accounts returned no Git-provider tokens. The OIDC account had its compatibility offline token; the SAML account did not. No `auth_tokens` or `local_credentials` rows were created by SSO. |
| HTTP 503 and 429 outages | A loopback proxy returned each failure at the actual token endpoint. Expired-cookie authentication and authorization-code callbacks returned 503; the browser cookie was retained. The existing API key still returned 200. Capabilities stayed `keycloak`, password login stayed disabled, and the local-password endpoint returned 404. |
| Stopped-server outage | Stopped both the Keycloak container and its backchannel proxy. The same unavailable response, retained cookie, API key continuity, and mode assertions passed. |
| Recovery | The same preserved cookie authenticated after clearing HTTP failures, restarting the Enterprise process, and restarting Keycloak. `/me` returned 200 without a new browser login. |
| Expired-cookie logout | After the fix, forced access-token expiration followed by logout returned 200 and removed the cookie; browser `/me` returned 401 and the existing API key returned 200. |
| Logout during outage | With access-token refresh returning HTTP 503, logout still cleared the browser cookie and returned 200. Anonymous `/me` returned 401 and the API key still returned 200. |
| Final database state | Revision 159, persisted Keycloak mode, original legacy UUID/organization/settings/API key and decrypted LLM settings all remained intact. No local credential existed. |

The live check found and verified the fix for an expired-cookie logout defect: middleware could write a refreshed cookie after the logout handler deleted it. The integration owner corrected middleware handling of authentication responses and allowed local browser logout to finish during upstream failures. The permanent regression is [test_logout_sessions.py](../tests/unit/server/auth/test_logout_sessions.py); both the expired-cookie and upstream-outage cases passed in the live browser after reloading the app.

The initial synthetic realm omitted Keycloak's standard `basic` client scope, which caused its access token to omit `sub`. Adding the standard scope corrected the harness. The broker setup also required the actual installed hardcoded attribute mapper ID and a declared `identity_provider` user-profile attribute. These were test configuration corrections, not product changes.

This validates the retained SAML and OIDC broker paths against a running Keycloak IdP. It does not certify configuration against a customer IdP, production certificates, or an external directory. No Helm or deployment-chart files were investigated or changed.

The browser used the real Keycloak login forms and the existing Terms of Service UI. Authenticated profile checks used real HTTP requests from its cookie context. The SSO assertion and token exchanges were not simulated. The fault proxy was used only for controlled outage cases; non-loopback browser requests were blocked during the final checks.

Cleanup is complete. The dedicated app, proxy, browser session, Keycloak and Redis containers were removed. After the other validation owners finished, the exact shared test PostgreSQL container recorded in `auth-core-postgres-state.json` was also removed. Generated passwords, signing secrets, private keys, browser storage state, imported realm files, and the owner mypy cache were deleted. Redacted logs, scripts, and results remain. [Cleanup record](auth-keycloak/cleanup.json).

Setup references: [Keycloak container guide](https://www.keycloak.org/server/containers), [Keycloak realm import](https://www.keycloak.org/server/importExport), and the official [administration guide](https://www.keycloak.org/docs/latest/server_admin/index.html). Installed Keycloak mapper metadata was queried to confirm the test configuration.
