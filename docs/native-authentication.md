# Native email and password authentication

Set `ENABLE_KEYCLOAK=false` for an installation that authenticates users with email and password. Use a fresh, identity-empty PostgreSQL database. Authentication mode is recorded during initialization and must remain consistent on every application process and scheduled job. The default is `ENABLE_KEYCLOAK=true`.

Set `NATIVE_AUTH_APP_ORIGIN` to the HTTPS origin users visit, without a path, query, fragment, or credentials. Supply a persistent `JWT_SECRET`, `SUPERADMIN_EMAIL`, and a `SUPERADMIN_PASSWORD` containing at least 15 characters through your deployment's secret configuration. Keep `APP_MODE` and `OH_APP_MODE` set to `saas`.

Run migrations and initialize the account before starting services:

```sh
uv run alembic upgrade head
uv run python -m server.auth.bootstrap
```

Sign in with the bootstrap account, accept the terms, and use Users to issue account setup links. Each recipient sets their own password. Organization invitations add membership to an authenticated account. Native account creation queues managed LLM provisioning for the application workers; failures remain pending and retry automatically.

Remove `SUPERADMIN_EMAIL` and `SUPERADMIN_PASSWORD` after initialization. Changing their values does not reset an existing account. Back up the database and preserve `JWT_SECRET` across restarts and restores.

## Browser and session configuration

`ENABLE_KEYCLOAK` and `NATIVE_AUTH_ALLOW_INSECURE_LOCALHOST` accept case-insensitive `true`, `1`, `false`, or `0`. Other values are rejected. The localhost exception defaults to false.

The app origin defaults to `https://${WEB_HOST}` when `WEB_HOST` is supplied, otherwise `http://localhost:3000`. For HTTP localhost development, explicitly set `NATIVE_AUTH_ALLOW_INSECURE_LOCALHOST=true`.

Trusted browser origins include the app origin and indexed `OH_PERMITTED_CORS_ORIGINS_<n>` settings. Indexed settings take precedence over comma-separated `PERMITTED_CORS_ORIGINS`. Configure explicit origins.

| Setting | Default |
| --- | --- |
| `NATIVE_AUTH_SESSION_IDLE_SECONDS` | `1800` |
| `NATIVE_AUTH_SESSION_ABSOLUTE_SECONDS` | `43200` |
| `NATIVE_AUTH_INVITATION_TTL_SECONDS` | `86400` |
| `NATIVE_AUTH_RESET_TTL_SECONDS` | `1800` |

Each duration accepts 60 through 2,592,000 seconds. Administrative actions that require recent authentication use a 15-minute window.

Disable `ENABLE_LINEAR` and `SLACK_WEBHOOKS_ENABLED` and remove Slack client and signing credentials. Native startup rejects these configurations. Jira integrations retain their configured workspace links and use the native account directory. Custom authentication adapters must explicitly support native authentication.

## Account recovery

Use Users to issue a one-time password reset link. An operator with application database access can reset an exact account UUID:

```sh
uv run python -m server.auth.bootstrap recover ACCOUNT_UUID
```

The command prompts for a new password and revokes existing browser sessions. It preserves account state and roles. Deleted accounts cannot be recovered.

Deleting a personal workspace can preserve an account for a fresh password login with the same UUID and ordinary permissions. Login waits for pending external cleanup. Disabling an account prevents login until an administrator enables it. Administrative account deletion is permanent.
