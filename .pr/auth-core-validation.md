# Authentication core validation

The local account core was validated against PostgreSQL 17.10 in a dedicated Docker container, as well as SQLite-backed unit tests. This is core validation; it does not claim that the complete application, browser, SDK, or external provider release gates have run.

## Reproduce

Start a disposable PostgreSQL 17 container on a dynamically assigned loopback port. These commands only create and remove the explicitly named test container.

```bash
AUTH_TEST_CONTAINER="oh-auth-core-pg-$(uuidgen | tr '[:upper:]' '[:lower:]')"
docker run --detach --rm --name "$AUTH_TEST_CONTAINER" \
  --env POSTGRES_HOST_AUTH_METHOD=trust \
  --publish 127.0.0.1::5432 postgres:17
AUTH_TEST_PORT="$(docker port "$AUTH_TEST_CONTAINER" 5432/tcp | sed 's/.*://')"
docker exec "$AUTH_TEST_CONTAINER" pg_isready --username postgres
OH_AUTH_TEST_POSTGRES_PORT="$AUTH_TEST_PORT" \
  uv run pytest tests/unit/server/auth/test_local_auth_postgres.py -q
docker rm --force "$AUTH_TEST_CONTAINER"
```

Wait for `pg_isready` to report that the server accepts connections before invoking pytest. `OH_AUTH_TEST_POSTGRES_PORT` is a test-only opt-in, independent of application database configuration. The tests connect exclusively to localhost, create databases with random UUID names, and remove only those databases. The module is skipped in normal unit runs.

The preserved PostgreSQL suite builds its own prior-version template by running the actual historical Alembic upgrade from the empty database through revision 157. It clones that schema for each test and applies the real 158 and 159 migration functions through the normal Alembic command. It uses the pg8000 migration driver and asyncpg for service calls. It does not use `Base.metadata.create_all()` to substitute for migrations, and no SQLite branches were added to migrations.

For the current disposable container, `.pr/auth-core-postgres-state.json` records its generated name and port; `uv run python .pr/test_auth_core_postgres.py` invokes the preserved suite against it. Container lifetime is coordinated with the account lifecycle validation agent.

Focused checks:

```bash
uv run pytest tests/unit/server/auth/test_local_accounts.py \
  tests/unit/server/auth/test_local_browser_security.py \
  tests/unit/server/auth/test_local_auth_postgres.py -q
uv run pytest tests/unit/test_smtp_email_service.py -q
uv run mypy --config-file dev_config/python/mypy.ini --follow-imports=silent \
  server/auth/local server/auth/browser_security.py server/routes/local_auth.py \
  server/services/smtp_email_service.py
uv run ruff check server/auth/local server/auth/browser_security.py \
  server/routes/local_auth.py server/services/smtp_email_service.py \
  tests/unit/server/auth/test_local_accounts.py \
  tests/unit/server/auth/test_local_browser_security.py \
  tests/unit/server/auth/test_local_auth_postgres.py
```

## PostgreSQL evidence

The disposable server reported PostgreSQL `17.10 (Debian 17.10-1.pgdg13+1)`; Docker reported `29.4.0`.

- The historical Alembic run committed revision 157 and 64 tables. Log: [historical migrations](logs/auth-core-historic-migration.log).
- The initial actual-service PostgreSQL suite passed all nine tests in 49.66 seconds. Log: [initial core tests](logs/auth-core-postgres-tests.log).
- After canonical email mutations and defaults moved into the shared user management/store helpers, the affected bootstrap and verification checks passed: three tests in 14.87 seconds. Log: [shared-helper checks](logs/auth-core-postgres-after-ums.log).
- The preserved regression suite passed all nine tests in 53.39 seconds. Log: [preserved tests](logs/auth-core-preserved-postgres.log).
- A final focused migration check passed one test in 22.36 seconds, including the manual-credential downgrade guard, retained Keycloak selection for populated legacy data, and rejected mode changes. Log: [final legacy migration check](logs/auth-core-postgres-legacy-final.log).

The PostgreSQL checks exercise these concrete invariants:

1. Eight concurrent `initialize_authentication(..., bootstrap=bootstrap_local_admin)` calls produce exactly one `User`, personal `Org`, owner `OrgMember`, `LocalCredentials`, and completed installation record. The user, personal org, and membership preserve the same UUID. The credential is Argon2id and initially restricted; only bootstrap assigns the instance admin role.
2. Changed bootstrap environment variables have no effect after completion. Demotion remains in place across restart. Explicit deletion of the account graph preserves installation history and never recreates an administrator. Failure after the real bootstrap callback creates its graph rolls back the entire graph and installation row; retry succeeds.
3. Eight parallel attempts to consume the same password-reset token produce one success and seven invalid-token failures. Existing sessions are all revoked. Tokens cannot be used for another purpose.
4. Eight parallel email-verification attempts produce one successful canonical update of the user, credential, and personal-org contact email. Old reset actions become invalid. The shared user management setter runs only inside the proven, locked token-consumption transaction.
5. Eight password changes using the same current password produce one success. The replacement session is unrestricted and the old session is revoked.
6. A reset racing eight logins using the old password cannot leave a usable session from the old credential. A login serialized before reset is revoked; a login serialized after reset fails verification. A token that expires while waiting for the account lock is rejected after the lock becomes available.
7. Eight concurrent invitation enrollments produce one account and one accepted invitation, preserve the invitation's explicit organization role, and grant no instance super role.
8. Legacy duplicate `User.email` values remain legal, while local normalized login identifiers are unique. Legacy user IDs, organization memberships, API keys, OAuth credential values, and refresh metadata survive migrations 158 and 159 and an explicit downgrade/re-upgrade round trip.
9. Manual provider credentials allow null refresh/expiry values, reject refresh tokens, and enforce unique provider-account/host associations. Downgrading with manual credentials is refused without deleting those records; after explicit manual disconnect, the migration round trip preserves legacy OAuth data.

The OAuth values used in migration tests are synthetic ciphertext markers. These checks validate schema preservation and constraints; they do not exercise real provider refresh APIs.

## Core fixes and unit coverage

- Local action endpoints return stable, redacted HTTP 400 codes: `invalid_token` and `invalid_password`. Login errors and password-recovery responses remain generic. Request parsing never returns rejected secret inputs.
- CSRF signatures bind named digests of local sessions, reconstructed chunked Keycloak cookies, and browser API-key cookies. Valid seeds are preserved across concurrent seed requests. The shared bridge must reseed CSRF when it refreshes a Keycloak cookie.
- Browser origin validation supports the existing explicit HTTP development origin and rejects malformed configuration with HTTP 403. Email links independently require an explicitly configured HTTPS `WEB_HOST`; SMTP alone cannot send tokens to the former external default hostname.
- New reset and verification tokens appear only in the URL fragment. Server-validated return destinations reject credential-bearing parameters, including encoded nested destinations. The origin for emailed links never comes from the request Host header.
- Authentication email HTML escapes link content. SMTP failure reporting for account messages uses a fixed message because an SMTP error can echo rejected message content, including a bearer link. It does not log the exception or traceback.
- User management owns the canonical verified email assignment; local actions own proof consumption, revocation, action invalidation, and transaction commit. Local account creation uses the pure shared default-settings helper and does not invoke external provisioning inside bootstrap.
- Bootstrap defaults strip the LLM API key from nested agent-settings JSON; the actual default key is stored only in the encrypted membership field. A focused regression checks both representations.
- Unit checks also cover disabled and malformed accounts, unknown-user dummy hash verification, Unicode/whitespace password policy, opaque-session expiry and revocation, stale email actions, enrollment takeover rejection, operator recovery restrictions, fail-closed Redis limits, mode gating, cookie attributes, and redirect validation.

The final combined core and SMTP unit run passed 79 tests, with the nine opt-in PostgreSQL tests skipped, in 14.76 seconds. Local routes share the same input-redacting `AuthValidationRoute` as provisioning routes.

## Remaining release gates

The actual local Enterprise app/browser, API-key/device, SDK ownership and sandbox-secret, repository-credential, conversation, SMTP, and operator-recovery gates subsequently passed in [the browser validation report](auth-browser-validation.md). The application integration and lifecycle agents own the separate Keycloak and lifecycle release checks. No deployment, commits, staging, pushes, Helm changes, or full pre-commit run were performed in this core-validation task.
