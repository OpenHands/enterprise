# OAuth Authentication Refactor — Design Plan

Epic: [OHE-3293](https://linear.app/all-hands-ai/issue/OHE-3293)
Phase 1: [OHE-3294](https://linear.app/all-hands-ai/issue/OHE-3294)

## Overview

The current authentication system is tightly coupled with Keycloak, conflates
git providers with identity providers (IDPs), uses a cookie structure that
overflows (requiring chunking), and has complex token-refresh timing that is
hard to reason about.

This refactor builds a **parallel OAuth authentication path** with new
`oauth_providers`, `oauth_provider_users`, and `oauth_tokens` tables, a unified
refresh workflow, and a small JWT cookie. The old Keycloak-coupled path is
deleted once the new path is validated.

## Problems being solved

| Concern | Today | Problem |
| -- | -- | -- |
| Login/IDP | Keycloak OIDC; tokens in signed JWS cookie | Cookie >4096B → chunking hack; Keycloak-only |
| IDP vs git provider | Keycloak brokers git OAuth; `ENTERPRISE_SSO` leaks into provider token map | Can't separate IDP from git provider |
| IDP refresh | `SaasUserAuth.refresh()` decodes JWT `exp` inline, lazily loads offline token | Implicit trigger, interleaved with request handling |
| Git refresh | `AuthTokenStore` + hardcoded 14400s/300s buffers, separate `SELECT FOR UPDATE` | Two unrelated refresh paths; hard to reason about |
| User identity | `User.id` = Keycloak `sub`; user mgmt via `KeycloakAdmin` | Identity source-of-truth is Keycloak |

## New schema

### `oauth_providers`

Per-provider OAuth configuration. A provider is either an IDP (`is_idp=True`)
or a git provider (`is_idp=False`), never both — SaaS "GitHub as both" = two
rows. `provider_category` reuses the existing `ProviderType` enum
(`ENTERPRISE_SSO` = any OIDC IDP that isn't also a git provider).

Columns: `id` (identity PK), `provider_category` (`ProviderType` value),
`display_name`, `is_idp` (bool), `client_id`, `client_secret` (EncryptedJSON),
`authorization_url`, `token_url`, `userinfo_url`, `scopes` (JSON array),
`permitted_drift_seconds` (int, default 60).

### `oauth_provider_users`

Maps a provider's external user ID to our `User`. Identity resolution table —
separate from credentials, survives token rotation, critical for login lookup.
Unique on `(oauth_provider_id, external_subject_id)`.

Columns: `id` (identity PK), `oauth_provider_id` (FK → `oauth_providers.id`),
`user_id` (FK → `user.id`), `external_subject_id`, `external_email`,
`created_at`, `updated_at`.

### `oauth_tokens`

Per-user credential pair for one provider. Denormalized
(`user_id` + `oauth_provider_id` directly) so token reads need no join.
`DateTime(tz=True)` for expiry (not epoch `BigInteger`), `NULL` for no-expiry
(kills the `0 == no expiry` sentinel). Uses existing `EncryptedJSON`
TypeDecorator for `access_token` and `refresh_token`.

Columns: `id` (identity PK), `user_id` (FK → `user.id`), `oauth_provider_id`
(FK → `oauth_providers.id`), `access_token` (EncryptedJSON), `refresh_token`
(EncryptedJSON), `access_token_expires_at` (`DateTime(tz=True)`, nullable),
`refresh_token_expires_at` (`DateTime(tz=True)`, nullable), `created_at`,
`updated_at`. Unique on `(user_id, oauth_provider_id)`.

### Cookie

Small signed JWT: `{ user_id, access_token_expires_at, accepted_tos }`. Cookie
`Max-Age` = IDP refresh-token expiry. `permitted_drift_seconds` default 60s
(clock drift margin only; expired access tokens are still refreshable via the
refresh token).

### Sandboxes (unaffected)

Sandbox token refresh is API-key-authenticated, not cookie-authenticated. It
reads the git-provider `oauth_tokens` row, independent of the IDP cookie.

## Refresh semantics

`OAuthTokenStore.get_valid_access_token`:

1. **Fast path**: read the row without a lock. If `access_token_expires_at` is
   `NULL` (never expires) or `> now + permitted_drift_seconds`, return it.
2. **Slow path**: the access token is expired (within drift). Acquire
   `SELECT ... FOR UPDATE` after `SET LOCAL lock_timeout = '5s'`.
3. **Double-check**: re-read; another worker may have refreshed while we waited.
4. If still expired and the refresh token is also expired (`NULL` treated as
   never), refresh via the provider's token endpoint and persist the new pair.

`NULL` expiry means "never expires" — no sentinel. An expired access token is
still refreshable as long as the refresh token is valid.

## Phases

- **Phase 1 — New tables, stores, and routes (additive)**: Purely additive, no
  behavior change. (OHE-3294)
- **Phase 2 — Dual-cookie middleware (new logins switch)**: Both old and new
  cookies work; no forced re-login.
- **Phase 3 — Migration (backfill from Keycloak)**: Pull users from Keycloak
  Admin API, backfill tokens.
- **Phase 4 — Delete old path (zero Keycloak references)**: Drop old
  tables/code, remove `keycloak` dependency.

## Resolved decisions

1. External `idp_sub`? → Dedicated `oauth_provider_users` table.
2. Cookie strategy? → JWT cookie, `Max-Age` = refresh-token expiry.
3. Enum vs `ProviderType`? → Reuse `ProviderType`; `ENTERPRISE_SSO` = non-git
   OIDC IDP.
4. `permitted_drift_seconds`? → 60s default (clock drift margin only).
5. Encryption type? → Use existing `EncryptedJSON`.
6. `user_authorizations`? → Drop `provider_type` column (always `NULL` in
   prod/staging).
7. Keep Keycloak? → Phase 1: generic provider row. Phase 4: delete all Keycloak
   code.
8. Cutover? → Dual-cookie middleware; old sessions naturally expire. No forced
   re-login.
