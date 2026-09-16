# Develop locally with email and password authentication

Copy [auth.env.example](auth.env.example) to an untracked `.env.auth` at the repository root. Supply credentials for a fresh PostgreSQL database and a persistent random `JWT_SECRET`. For first startup, also supply `SUPERADMIN_EMAIL` and a `SUPERADMIN_PASSWORD` containing at least 15 characters. Email is the login identifier.

Retain your existing LLM provisioning configuration. Set `OH_WEB_URL` to the browser's application URL. The example enables HTTP cookies for localhost development.

```sh
uv sync --all-groups
uv run --env-file .env.auth alembic upgrade head
uv run --env-file .env.auth uvicorn saas_server:app --reload --port 3000
```

After migrations, normal application startup creates the initial administrator transactionally and preserves it on subsequent starts.

Use the [frontend development setup](README.md) with a backend proxy, or serve a built frontend from the application. Keep the configured origin aligned with the browser URL.

Keep `ENABLE_KEYCLOAK=false` on every application process and scheduled job. `SUPERADMIN_EMAIL` and `SUPERADMIN_PASSWORD` are used only for first initialization and can be removed afterward. Changing them does not reset an existing account. Preserve `JWT_SECRET` with database backups. See [authentication configuration](../../docs/authentication.md) for session settings.
