# Run Enterprise locally with Docker Compose

Use this Compose stack to try Enterprise locally with email/password login and
Docker sandboxes. Organization administrators configure shared LLM providers and
profiles in Settings after login.
The stack runs the application, PostgreSQL, and Redis without Keycloak or a
LiteLLM gateway.

To also test SSO, add the optional [local MockSAML setup](SAML.md).

## Start

Install a current Docker Engine with Compose, or Docker Desktop, and Python 3.
Use a local Docker daemon with Linux containers. The browser must run on the same
machine. Both Apple Silicon and x86-64 hosts can use the bundled Agent Server image.

From the repository root:

```sh
python3 containers/compose/setup.py
```

The setup script creates a private, untracked `.env` file. Open it in your editor
to read the generated `SUPERADMIN_PASSWORD` and change `SUPERADMIN_EMAIL` before
the first start. The example login email is `admin@example.test`; no email service
is needed. Running setup again preserves the existing file and secrets.

Start the services:

```sh
docker compose up --build -d
docker compose ps
```

Open `http://localhost:3000`, or the port selected by `OPENHANDS_PORT` in `.env`,
and sign in with those credentials. The first start builds the UI and backend,
applies migrations, and creates the administrator. Bootstrap credentials are
passed only to the initialization service. Changing them in `.env` after
initialization does not change an existing account's login.

As an organization administrator, open **Settings** to configure the shared LLM
provider, API key, and organization profiles. Start a conversation to create its
Docker sandbox. The first conversation may take longer while Docker pulls the
Agent Server image. A repository connection is optional.

To inspect startup progress:

```sh
docker compose logs --tail=100 init openhands
```

## Configuration

Edit `.env` before starting the stack. [`.env.example`](.env.example) lists the
supported local settings.

- `OPENHANDS_PORT` selects the localhost application port.
- `DOCKER_SOCKET_PATH` selects the host socket mounted into the application.
  Setup detects the active local Unix socket, including Docker Desktop and
  OrbStack. Set this explicitly if your socket is elsewhere.
- `COMPOSE_PROJECT_NAME` selects the installation's container, network, and volume
  prefix. Choose a distinct name and application port for a second installation.
- `COMPOSE_AGENT_SERVER_IMAGE` selects a custom image compatible with the bundled
  SDK. Leave it unset to use the matching published Agent Server image. Existing
  sandboxes retain the image and settings with which they were created.

Keep `.env` with the database backup. Its `JWT_SECRET` decrypts stored credentials
and must remain unchanged across restarts. Keep the database name and credentials
unchanged after PostgreSQL initializes its volume.

This setup uses localhost HTTP and a local Docker socket. The application runs as
root to access the socket and creates sandbox containers under their image's user.
Sandbox service ports are assigned dynamically and published on all host
interfaces so the application container can reach them through the host gateway.
Use this stack on a trusted local machine. Remote access and TLS require separate
routing configuration; see [sandbox provider configuration](../../openhands/app_server/sandbox/providers.md).

## Stop, restart, and remove

Application and database state live in named volumes. Each sandbox also has its
own workspace volume. Restarting the application preserves running sandboxes:

```sh
docker compose restart openhands
```

To stop the application services and start them again later:

```sh
docker compose stop
docker compose start
```

Sandbox containers run independently of Compose and remain running when those
services stop. Pause conversations in the UI before stopping the stack when you
want their sandboxes suspended. Reopening a paused conversation resumes it.
There is no automatic idle or retention cleanup in this local provider.

To remove a sandbox and its workspace, delete its conversations in the UI and
wait for cleanup to finish. The sandbox is removed after its last conversation
is deleted. For a sandbox left by an interrupted operation, use the authenticated
`GET /api/v1/sandboxes/search` and `DELETE /api/v1/sandboxes/{id}` endpoints in the
application's `/docs` API reference with an API key from Settings. Direct sandbox
deletion removes its workspace.

After deleting the sandboxes, remove the application services:

```sh
docker compose down
```

`down` keeps named data volumes. Adding `--volumes` permanently removes the
application and database data. Delete sandboxes before removing that inventory.
Compose does not delete sibling sandbox containers or their workspace volumes;
a remaining sandbox can also keep the sandbox network in use. Use the app's
cleanup endpoints, then retry `down`. Do not use Docker prune for this stack.
