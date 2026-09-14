# Develop locally with native authentication

For a complete local Enterprise instance, use the [Docker Compose setup](../../containers/compose/README.md).
For backend development, use a fresh PostgreSQL database and follow the steps below.

Copy [native.env.example](native.env.example) to an untracked `.env.native` at the repository root.
Supply the database credentials, a persistent random `JWT_SECRET`, `SUPERADMIN_EMAIL`,
and a password of at least 15 characters. Set `NATIVE_AUTH_APP_ORIGIN` to the browser's
application origin. The example enables HTTP cookies for localhost development.

Install dependencies, apply migrations, and create the administrator:

```sh
uv sync --all-groups
uv run --env-file .env.native alembic upgrade head
uv run --env-file .env.native python -m server.auth.bootstrap
```

Run the backend with the same environment:

```sh
uv run --env-file .env.native uvicorn saas_server:app --reload --port 3000
```

Use the [frontend development setup](README.md) with a backend proxy, or serve a built
frontend from the application. Keep the configured application origin aligned with
the browser URL. Configure LLM providers and profiles in **Settings** after login.
Git connections are optional for conversations without a repository.

Keep `ENABLE_KEYCLOAK=false` on each application process and scheduled job for this
installation. Remove the bootstrap administrator credentials from `.env.native` after
initialization. Preserve `JWT_SECRET` with your database backups.

See [native authentication](../../docs/native-authentication.md) for session settings,
account administration, and recovery. See [native SAML](../../docs/native-saml.md) for
identity provider configuration, or [local MockSAML](../../containers/compose/SAML.md)
to test SSO with the Compose stack.
