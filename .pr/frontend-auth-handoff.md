# Enterprise local-auth frontend handoff

The frontend implements the initial local-account UI. Backend routes are being implemented by `local_accounts` and integration by the root/bridge work. No commit, staging, push, or deployment was performed.

## Browser flows

- `/login` fetches Enterprise auth capabilities. Local mode always renders password login, even with a remembered Keycloak provider. Keycloak login uses only `login_providers` returned by the backend. Enterprise capability failures show an unavailable state; confirmed OSS retains its existing behavior.
- `/auth/change-password?returnTo=...` handles initial/bootstrap/administrator temporary passwords and ordinary password changes. Restricted sessions can log out. Server completion redirects determine terms/onboarding/return destinations.
- `/auth/forgot-password?returnTo=...` requests recovery with a generic result. It and email-edit/resend controls are unavailable without `email_recovery`.
- `/auth/reset-password?returnTo=...#token=...` resets a password. `/auth/verify-email?returnTo=...#token=...` confirms verification explicitly on submit. **New action tokens are read only from the URL fragment; there is no query-token fallback.** The token stays in component memory if invitation cleanup replaces the URL.
- `/auth/verify-email?returnTo=...` without a token requests a verification email for the signed-in user.
- `/auth/enroll?invitation_token=...&returnTo=...` enrolls invited accounts with a new password. The existing invitation query/localStorage contract is preserved; no public signup is offered.
- `/settings/user` shows local account email, verified replacement-email requests, resend verification, and a password-change link. Unverified administrator-provisioned accounts are not subject to the legacy block-all-access email guard; backend policy governs mailbox-dependent actions.
- `/settings/accounts` creates local users with temporary passwords. Both navigation and form require backend-provided `useMe().permissions` containing `manage_users`.
- `/settings/integrations` uses existing manual token controls and V1 secrets POST/DELETE in local mode. Keycloak retains broker linking. Login providers and repository connection capability are independent.
- Links into `/auth/*` use full document navigation. `PostHogWrapper` does not initialize analytics on those pages, and the route sets `no-referrer` metadata.

New passwords use 15–1024 Unicode code points, allow spaces/Unicode, and are never trimmed. Login accepts existing passwords without the new-password minimum.

## HTTP contracts

All auth paths are application-origin endpoints. JSON requests use the existing axios client with credentials. `GET /api/auth/csrf` returns `{csrf_token}` and seeds/reuses host-only readable `oh_csrf`; auth writes explicitly send `X-CSRF-Token`. The axios client also forwards this cookie on existing browser mutations. Capabilities fetch seeds CSRF so direct terms/intermediate pages have it.

`GET /api/auth/capabilities` returns:

```json
{
  "mode": "local",
  "password_login": true,
  "login_providers": [],
  "registration": "admin_or_invitation",
  "email_recovery": true,
  "repository_connections": {"manual_tokens": true, "broker": false}
}
```

`mode` is `local` or `keycloak`. Capabilities are enabled only when config confirms `app_mode: saas`.

In this table, `context` means `redirect_url: string` plus optional `invitation_token: string`. `result` means `{redirect_url: string, password_change_required?: boolean}`. The backend validates redirects and chooses the next admission/terms/onboarding destination. The browser additionally rejects non-relative, network-path, backslash, and control-character redirects.

| Method/path | JSON request | Response |
| --- | --- | --- |
| POST `/api/auth/login` | `{email,password,...context}` | result |
| POST `/api/auth/password/change` | `{current_password,new_password,...context}` | result |
| POST `/api/auth/password/forgot` | `{email,...context}` | generic success body ignored |
| POST `/api/auth/password/reset` | `{token,new_password,...context}` | result; may point to login |
| POST `/api/auth/email/request-verification` | context | generic success body ignored |
| POST `/api/auth/email/verify` | `{token,...context}` | result |
| POST `/api/auth/email/change` | `{email,...context}` | generic success body ignored; existing email remains until verified |
| POST `/api/auth/invitations/enroll` | `{invitation_token,password,redirect_url}` | result |
| POST `/api/auth/accounts` | `{email,initial_password}` | `{id,email,password_change_required}` |
| POST `/api/authenticate` | `{}` | existing successful response; 401 means unauthenticated |
| POST `/api/logout` | `{}` | existing successful response |

Restricted browser-session API responses use `403 {"detail":{"code":"password_change_required","redirect_url":"/auth/change-password?returnTo=..."}}`. The axios interceptor follows this specific code only; ordinary 403, provider 409, and provider 503 do not log out the browser.

Provider navigation is backend-owned:

- `GET /api/auth/authorize?provider=...&redirect_url=...&invitation_token=...&recaptcha_token=...` redirects to the configured provider. Optional values are omitted. The frontend no longer assembles realm/client/host URLs or OAuth state.
- `GET /api/auth/providers/{provider}/link?redirect_url=...` starts authenticated broker linking.
- Existing `POST /api/v1/secrets/git-providers` sends `{provider_tokens:{...}}`; DELETE on that path clears manual tokens. Existing broker disconnect remains `DELETE /api/v1/users/git-providers/{provider}`.

The token form sends all six provider entries, including empty values for unconfigured providers. The provider backend owner was notified to skip unused empty entries while retaining existing credentials for host-only/no-token updates. Settings metadata remains `provider_tokens_set` with provider-to-host entries.

## Main files

- API contracts: `frontend/src/api/auth-service/{auth-service.api.ts,auth.types.ts,csrf.ts}`, axios CSRF/redirect handling in `frontend/src/api/open-hands-axios.ts`.
- Query/mutations: `use-auth-capabilities.ts`, `use-local-auth.ts`, and the legacy email data-access cleanup in `use-update-email.ts` / `email-service.api.ts`.
- UI: `routes/account-action.tsx`, `routes/manage-accounts.tsx`, `components/features/auth/{account-form,password-login,local-account-settings}.tsx`.
- Existing login/root/settings/provider hooks and routes are capability-aware. `auth-redirect.ts` and `password-policy.ts` contain shared validation.
- New strings have translations for every supported locale; generated declarations updated.

## Validation

- Final combined auth/provider/root/settings/analytics regression: 13 files, **145 passed, 1 existing skipped**. Includes the final fragment-only email links and analytics exclusion.
- `npm run lint:fix`: passes; existing unrelated warnings only.
- `npm run build`: passes.
- `npm run typecheck`: passes.
- `npm run check-translation-completeness`: passes.
- `git diff --check -- frontend`: passes.

This environment has Node 25.2.1; its native webstorage collides with jsdom. Test commands use `NODE_OPTIONS=--no-webstorage npm run test -- ...`; no repository runtime configuration was changed. Dependencies were installed with `npm ci --ignore-scripts`, preserving the lockfile.

Full browser validation against the real local backend is still required at the release gate. In particular, verify bootstrap-to-conversation, actual SMTP fragment links, initial/ordinary password session rotation, admin creation/disabling, no-SMTP login, manual Git persistence, and Keycloak broker callbacks. The frontend tests use the agreed contracts and mock backend responses.
