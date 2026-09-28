# Instructions for developing SAAS locally

You have a few options here, which are expanded on below:

- A simple local development setup, with live reloading for both OpenHands and this repo
- A more complex setup that includes Redis
- An even more complex setup that includes GitHub events

## Prerequisites

Before starting, make sure you have the following tools installed:

### Required for all options:

- [gcloud CLI](https://cloud.google.com/sdk/docs/install) - For authentication and secrets management
- [sops](https://github.com/mozilla/sops) - For secrets decryption
  - macOS: `brew install sops`
  - Linux: `sudo apt-get install sops` or download from GitHub releases
  - Windows: Install via Chocolatey `choco install sops` or download from GitHub releases

### Additional requirements for enabling GitHub webhook events

- make
- Python development tools (build-essential, python3-dev)
- [ngrok](https://ngrok.com/download) - For creating tunnels to localhost

## Option 1: Simple local development

This option will allow you to modify both the OpenHands code and the code in this repo,
and see the changes in real-time.

This option works best for most scenarios. The only thing it's missing is
the GitHub events webhook, which is not necessary for most development.

### 1. Install dependencies

From the repository root:

```
make build
```

This runs `uv sync --all-groups`, installs the frontend dependencies and builds the frontend.

### 2. Set up env

First run this to retrieve Github App secrets

```
gcloud auth application-default login
gcloud config set project global-432717
dev_config/local_saas/decrypt_env.sh /path/to/root/of/deploy/repo
```

Now run this to generate a `.env` file (in the repository root), which will be used to run SAAS locally

```
export LITE_LLM_API_KEY=<your LLM API key>
uv run python dev_config/local_saas/convert_to_env.py
```

By default the application will log in json, you can override.

```
export LOG_PLAIN_TEXT=1
```

### 3. Start the OpenHands frontend

```
make start-frontend
```

### 4. Start the SaaS backend

```
make start-saas-backend
```

You should have a server running on `localhost:3000`, similar to the open source backend.
Oauth should work properly.

## Option 2: With Redis

Follow all the steps above, then setup redis:

```bash
docker run  -p 6379:6379 --name openhands-redis -d redis
export REDIS_HOST=host.docker.internal # you may want this to be localhost
export REDIS_PORT=6379
```

## Option 3: Work with GitHub events

### 1. Setup env file

(see above)

### 2. Build SAAS Openhands

Build the image from the repository root:

```
docker build -f containers/app/Dockerfile -t openhands-saas .
```

### 3. Create a tunnel

Run in a separate terminal

```
ngrok http 3000
```

There will be a line

```
Forwarding                    https://bc71-2603-7000-5000-1575-e4a6-697b-589e-5801.ngrok-free.app
```

Remember this URL as it will be used in Step 4 and 5

### 4. Setup Staging Github App callback/webhook urls

Using the URL found in Step 3, add another callback URL (`https://bc71-2603-7000-5000-1575-e4a6-697b-589e-5801.ngrok-free.app/oauth/github/callback`)

### 5. Run

This is the last step! Run SAAS openhands locally using

```
docker run --env-file ./.env -p 3000:3000 openhands-saas
```

Note `--env-file` is what injects the `.env` file created in Step 1

Visit the tunnel domain found in Step 3 to run the app (`https://bc71-2603-7000-5000-1575-e4a6-697b-589e-5801.ngrok-free.app`)

### Local Debugging with VSCode

Local Development necessitates running a version of OpenHands that is as similar as possible to the version running in the SAAS Environment. Run the launch configurations below from the repository root (the `.venv` created by `uv sync`).

#### Redis

A Local redis instance is required for clustered communication between server nodes. The standard docker instance will suffice.
`docker run -it -p 6379:6379 --name my-redis -d redis`

#### Postgres

A Local postgres instance is required. I used the official docker image:
`docker run -p 5432:5432 --name my-postgres -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=openhands -d postgres`
Run the alembic migrations:
`uv run alembic upgrade head`

> **Note:** By default, migrations use the `pg8000` driver (matching production,
> which connects through the Cloud SQL connector on pg8000). To use psycopg2 instead,
> set `DB_DRIVER=''` before running alembic commands.

#### VSCode debugger workflow (recommended)

The repo includes a `.vscode/launch.json` checked into git and an `.env.template`
file. This workflow keeps secrets out of version control while making it trivial
to start the SaaS server with breakpoints.

> **Personal configs:** If you previously had a `.vscode/launch.json` with inline
> secrets, rename it to `.vscode/.launch.json` -- it's gitignored (covered by the
> `.vscode/**/*` rule) and serves as a personal backup. The shared `launch.json`
> loads all secrets from `.env` via `envFile`, so you never need inline secrets
> in the debugger config itself.

**1. Create your `.env` file:**

```bash
cp .env.template .env
```

Edit `.env` and fill in the real values for secrets only:
- `KEYCLOAK_CLIENT_SECRET`, `KEYCLOAK_ADMIN_PASSWORD` -- from staging Keycloak
- `LITE_LLM_API_KEY`, `LITE_LLM_TEAM_ID` -- LLM proxy credentials
- `SANDBOX_API_KEY` -- staging remote runtime API key
- `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET` -- GitHub App
- `STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET` -- Stripe billing
- `POSTHOG_CLIENT_KEY` -- PostHog analytics
- `TAVILY_API_KEY` -- MCP search engine
- `DB_PASS` -- local PostgreSQL password (default: `postgres`)

All non-sensitive config (hostnames, ports, paths, feature flags) is baked
into `.vscode/launch.json` inline via the `env` block, which overrides values
from `.env`. This keeps secrets isolated in `.env` while sharing the rest.

Secrets may also be harvested directly from staging by connecting:
`kubectl exec --stdin --tty <POD_NAME> -n <NAMESPACE> -- /bin/bash`
And then invoking `printenv`. NOTE: _DO NOT DO THIS WITH PROD!!!_

**2. Build the frontend + Canvas with lock-to-cloud:**

The Agent Canvas SPA must be built with `VITE_LOCK_TO_CLOUD` set to the origin
the browser will visit. This locks Canvas to a single cloud backend with cookie
auth, suppressing the "Add a Backend" onboarding prompt.

```bash
# Start local PostgreSQL (first time only)
make local-db

# Build frontend
make build-frontend

# Build Canvas locked to localhost:3030 (cookie auth mode)
AGENT_CANVAS_LOCK_TO_CLOUD=http://localhost:3030 make build-agent-canvas
```

> **Why `WEB_HOST=localhost`?** The `.env.template` sets `WEB_HOST=localhost`
> (not `localhost:3030`). This makes `IS_LOCAL_ENV=true` in `server/constants.py`,
> which causes:
> - Cookies to use `SameSite=Lax` (not `Strict`) — needed for OAuth redirects
> - Cookies to have no domain — scoped to `localhost`
> - Cookies to have `Secure=false` — correct for HTTP on localhost
>
> Without `WEB_HOST=localhost`, cookies default to `SameSite=Strict` and the
> Keycloak OAuth callback cannot set the `keycloak_auth` cookie.

**3. Start the server with the VSCode debugger:**

Open the Run & Debug panel in VSCode and select **"SaaS Server (port 3030)"**.
The `launch.json` uses `envFile: "${workspaceFolder}/.env"` to load all env vars
from your `.env` file automatically. Set breakpoints in your code and press F5.

Available debugger targets:
- **SaaS Server (port 3030)** -- the SaaS/enterprise server (primary)
- **Unit Tests (all)** -- runs the full `./tests/unit` suite
- **Unit Tests (single file)** -- runs the test file currently open in the editor
- **Frontend Tests (npm test)** -- runs the frontend vitest suite
- **Python Debugger: Current File** -- runs whatever Python file is open

The server runs on `http://localhost:3030`. Navigate to `http://localhost:3030/canvas/`
in your browser -- Canvas will use cookie auth (no "Add a Backend" prompt).

> **Important:** Do NOT set `SESSION_API_KEY` in your `.env`. The SaaS server
> uses cookie-based auth (Keycloak), not session API keys. A stale
> `SESSION_API_KEY` can cause `/api/v1/settings` to return 401 in the browser.

**4. (Optional) Clear stale browser state:**

If you previously visited `http://localhost:3030/canvas/` and saw the "Add a
Backend" prompt, clear the stale localStorage in browser devtools:

```js
localStorage.removeItem("openhands-backends");
localStorage.removeItem("openhands-active-backend");
localStorage.removeItem("openhands-onboarded");
```

Then hard-refresh (Cmd+Shift+R).
