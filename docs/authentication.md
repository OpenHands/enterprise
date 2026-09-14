# Email and password authentication

Set `ENABLE_KEYCLOAK=false` to let users sign in with an email address and password. Email is the login identifier. Keep `ENABLE_KEYCLOAK=true`, the default, for an existing Keycloak installation.

Use a fresh PostgreSQL database for password authentication. Authentication mode is recorded during initialization and must match on all application processes and scheduled jobs.

Use the application's existing public URL configuration. Set `OH_WEB_URL` to the URL users visit, or keep `WEB_HOST` for its `https://<WEB_HOST>` default:

```dotenv
ENABLE_KEYCLOAK=false
APP_MODE=saas
OH_APP_MODE=saas
OH_WEB_URL=https://app.example.com
AUTH_ALLOW_INSECURE_LOCALHOST=false
```

Supply a persistent `JWT_SECRET` through your secret configuration. For first startup, also supply `SUPERADMIN_EMAIL` and a `SUPERADMIN_PASSWORD` containing at least 15 characters. Apply migrations through your existing deployment process, then start the application normally. Startup creates the initial administrator transactionally and preserves it on subsequent starts.

Sign in with the administrator's email address and password, then accept the terms. `SUPERADMIN_EMAIL` and `SUPERADMIN_PASSWORD` are used only for first initialization and can be removed afterward. Changing them does not reset an existing account. Preserve `JWT_SECRET` with database backups.

## Browser sessions

Browser-origin checks use the scheme, hostname, and port from the application's public URL. The URL must not contain credentials, a query string, or a fragment. For HTTP localhost development, set `OH_WEB_URL=http://localhost:3000` and `AUTH_ALLOW_INSECURE_LOCALHOST=true`. Boolean settings accept case-insensitive `true`, `1`, `false`, and `0`.

Trusted browser origins include the application origin and indexed `OH_PERMITTED_CORS_ORIGINS_<n>` settings. Indexed settings take precedence over comma-separated `PERMITTED_CORS_ORIGINS`.

| Setting | Default |
| --- | --- |
| `AUTH_SESSION_IDLE_SECONDS` | `1800` |
| `AUTH_SESSION_ABSOLUTE_SECONDS` | `43200` |

Each duration accepts 60 through 2,592,000 seconds. Logout revokes the browser session. Password forms clear credentials after submission and do not save them in browser storage.

Disable `ENABLE_LINEAR` and `SLACK_WEBHOOKS_ENABLED` and remove Slack client and signing credentials for this configuration. Keep existing LLM provisioning configuration in place.

For backend development, follow the [local authentication setup](../dev_config/local_saas/AUTH.md).

## Account setup and organization invitations

Administrators with the instance-wide `manage_users` permission can open **Settings > Users** to create account setup links. Organization owner permissions alone do not grant access to this screen.

Enter the recipient's email address. Select a team and role to include organization membership, or leave the invitation scoped to a personal workspace. Share the private link directly with the intended recipient. The link is shown once; reissue it when a replacement is needed.

New recipients set their own password. Existing accounts sign in before accepting the intended organization membership. Matching an email address does not bypass authentication. Users then accept the terms before entering the application.

Set the setup-link lifetime in seconds:

```dotenv
AUTH_INVITATION_TTL_SECONDS=86400
```

The Users screen lists accounts and invitations. Administrators can inspect account details, revoke an unused invitation, or reissue an expired link. Links expire after the configured duration and can be consumed only once.

## Password changes and recovery

Users can change their password in **Settings > User** by entering their current password and confirming the new password.

For account recovery, an administrator selects the account in **Settings > Users** and creates a password reset link. Creating the link requires authentication within the last 15 minutes; the screen prompts the administrator to sign in again when necessary. Share the private link directly with its intended recipient.

Set the reset-link lifetime in seconds:

```dotenv
AUTH_RESET_TTL_SECONDS=1800
```

The recipient chooses and confirms a new password, then signs in. Completing the reset revokes existing browser sessions. Reset links expire and can be consumed only once.

An operator with application database access can recover an account by its exact UUID:

```sh
uv run python -m server.auth.bootstrap recover ACCOUNT_UUID
```

The command prompts for the new password, revokes browser sessions, and preserves account state and roles. Deleted accounts cannot be recovered.
