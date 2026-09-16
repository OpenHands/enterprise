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

Browser sessions use session cookies and have no server-side time limit. Logout ends the current session; other sessions remain active. Password forms clear credentials after submission and do not save them in browser storage.

Disable `ENABLE_LINEAR` and `SLACK_WEBHOOKS_ENABLED` and remove Slack client and signing credentials for this configuration. Keep existing LLM provisioning configuration in place.

For backend development, follow the [local authentication setup](../dev_config/local_saas/AUTH.md).
