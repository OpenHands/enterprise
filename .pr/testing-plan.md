# Testing Plan: Logout and Retry When IdP Broker Tokens Are Unusable

> **Status: NOT YET TESTED.** This is a proposed manual end-to-end testing plan.
> It has not been executed against a live SaaS/Keycloak environment. The
> automated unit tests listed at the end have been run; the manual provider,
> Keycloak, browser, and recovery steps below still require validation.

## Purpose

Verify that a stale or revoked federated grant no longer causes the legacy
`/oauth/keycloak/callback` endpoint to return a permanent 500, and that the
user is instead logged out and returned to the login page to re-authenticate.

The plan also verifies the complementary case: if the provider tokens stored
by an earlier OpenHands login are still usable, a broker-token failure must
not unnecessarily log the user out.

## Scope

This plan covers:

- GitLab as the primary reproduction provider.
- Bitbucket Cloud and GitHub as equivalent provider-side revocation checks,
  where available.
- Keycloak's broker-token retrieval path.
- OpenHands' stored provider-token path in the `auth_tokens` table.
- The login-page message controlled by `git_provider_login_failed=true`.

It does not cover enterprise SSO/SAML providers, for which broker-token
storage is skipped.

## Test Environment

Use a non-production SaaS environment with:

- This PR deployed.
- Postgres, Redis if required by the environment, Keycloak, and the frontend
  running.
- A dedicated test user for each provider being tested.
- Access to:
  - the OpenHands login page;
  - Keycloak logs;
  - the OpenHands database, so the test user's `auth_tokens` rows can be
    inspected or deliberately expired/removed;
  - the upstream provider account used by the test user.
- A way to observe the callback response, redirect location, and whether the
  `keycloak_auth` cookie is cleared.

Do not use a personal production account or a production provider grant.

## Important Test Setup Note

Revoking a provider application does not necessarily invalidate an already
issued access token immediately. It reliably invalidates the stored
authorization/refresh-token relationship when a refresh is attempted.

For a deterministic reproduction, before signing in again, ensure the stored
provider access token is expired or remove the corresponding `auth_tokens`
row for the test user and provider. The relevant columns are:

- `keycloak_user_id`
- `identity_provider` (`gitlab`, `bitbucket`, or `github`)
- `access_token_expires_at`
- `refresh_token_expires_at`

Do not delete or alter rows for any other user.

## Scenario 1: Revoked Grant With No Usable Stored Provider Tokens

### Setup

1. Sign in to OpenHands with the dedicated GitLab test user and confirm that
   the normal login succeeds.
2. Confirm that the user has an `auth_tokens` row for `gitlab`.
3. Sign out of OpenHands.
4. In GitLab, as the test user, go to **Edit profile → Access →
   Applications** and revoke the OpenHands/Keycloak application.
5. In the test database, either:
   - wait until the stored GitLab access token expires; or
   - expire/remove only that test user's `gitlab` row from `auth_tokens`.

### Steps

1. Open the OpenHands login page.
2. Sign in with GitLab.
3. Observe the callback and the final browser location.
4. Inspect the response cookies.
5. Inspect the Keycloak and OpenHands logs.
6. Use the login page to sign in again and complete the fresh GitLab
   authorization flow.

### Expected Results

- `/oauth/keycloak/callback` does not return HTTP 500.
- OpenHands logs `idp_token_store_failed_logout` for the test user and
  provider.
- The just-established Keycloak session is terminated through
  `TokenManager.logout`.
- The response is a 302 redirect to:

  `/login?git_provider_login_failed=true`

- The `keycloak_auth` cookie is cleared.
- The login page displays:

  > We couldn't refresh your git provider connection. Please sign in again to
  > reconnect it.

- Signing in again performs a fresh provider authorization and succeeds.
- After the successful retry, the provider tokens are stored again and the
  user reaches the application normally.

## Scenario 2: Broker Failure With Still-Usable Stored Provider Tokens

### Setup

1. Sign in successfully with a dedicated test user.
2. Confirm that the stored provider token can still refresh directly against
   the provider. For example, confirm that
   `TokenManager.get_idp_token_by_user_id` succeeds for that user/provider.
3. Arrange for Keycloak's broker-token endpoint to fail for the callback
   without revoking the provider authorization. This may be done only in a
   disposable test environment, for example by temporarily making the broker
   endpoint unavailable or by using a controlled test double at the Keycloak
   boundary.
4. Do not use the provider-side revocation from Scenario 1 for this scenario.

### Steps

1. Sign in through Keycloak.
2. Trigger the callback while the broker-token request is failing.
3. Observe the final redirect and the application session.

### Expected Results

- The login completes normally.
- The user is not redirected to
  `/login?git_provider_login_failed=true`.
- `TokenManager.logout` is not called.
- OpenHands logs `idp_token_store_failed`, but the login continues.
- The `keycloak_auth` cookie is set as in a normal successful login.

## Scenario 3: Provider Equivalents

Repeat Scenario 1 with a dedicated user for each provider available in the
test environment.

### Bitbucket Cloud

1. Sign in to Bitbucket as the test user.
2. Go to **Personal Bitbucket settings → App authorizations**.
3. Revoke the OpenHands/Keycloak application.
4. Expire or remove only that test user's `bitbucket` row from
   `auth_tokens`.
5. Sign in to OpenHands again.

Expected result: the same logout, cookie-clearing, redirect, login message,
and successful fresh-authorization retry as Scenario 1.

### GitHub

1. Sign in to GitHub as the test user.
2. Go to **Settings → Applications → Authorized OAuth Apps**.
3. Revoke the OpenHands/Keycloak application.
4. Expire or remove only that test user's `github` row from `auth_tokens`.
5. Sign in to OpenHands again.

Expected result: the same logout, cookie-clearing, redirect, login message,
and successful fresh-authorization retry as Scenario 1.

## Scenario 4: Keycloak Unlink Is Not a Substitute

This scenario documents an important distinction and should not be treated as
a reproduction of the original 502.

1. In Keycloak Admin Console, open the test user.
2. Remove the federated identity-provider link, or call:

   `DELETE /admin/realms/{realm}/users/{user-id}/federated-identity/{provider}`

3. Leave the upstream provider authorization and OpenHands' stored provider
   tokens unchanged.
4. Sign in again.

Expected result: this is not required to reproduce the stale-grant 502. The
callback code treats “user is not associated with identity provider” as a
no-op for broker-token storage, and previously stored OpenHands provider
tokens may still be usable.

## Observation Checklist

For each scenario, record:

- Provider and test-user identifier.
- Whether the provider authorization was revoked.
- Whether the stored `auth_tokens` access token was expired or removed.
- Callback HTTP status.
- Final redirect location.
- Whether `keycloak_auth` was set or cleared.
- Whether the login-page error message appeared.
- Relevant OpenHands log event:
  - `idp_token_store_failed_logout` for the logout path; or
  - `idp_token_store_failed` for the best-effort path.
- Relevant Keycloak broker error, if present.
- Whether retrying login completed fresh provider authorization and reached
  the application.

## Automated Tests Already Run

The following tests cover the callback logic with mocked token-manager
dependencies:

```bash
uv run pytest tests/unit/test_auth_routes.py \
  -k 'idp_token_store_failure or idp_store_failure' -q
```

The full auth-route test file can be run with:

```bash
uv run pytest tests/unit/test_auth_routes.py -q
```

These automated tests do not replace the manual live-environment scenarios
above.
