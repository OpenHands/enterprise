# Configure sandbox providers and templates

Set `SANDBOX_PROVIDER=runtime_api` for Runtime API or `SANDBOX_PROVIDER=docker`
for managed Docker. Define stable template IDs in `SANDBOX_TEMPLATES` and select
one with `SANDBOX_DEFAULT_TEMPLATE`. An entry inherits the selected provider when
its `provider` field is omitted. The catalog is loaded when the app server starts.

Use a new template ID when changing an image or workspace layout, for example
`python-v2`. The app stores the selected ID on the sandbox. Existing managed
sandboxes retain their original workspace configuration after a catalog change.
Template commands, startup environment values, and sandbox credentials are
private and do not appear in the public sandbox-spec API.

## Runtime API

Configure Runtime API access on the app server:

```sh
unset OH_SANDBOX_KIND OH_SANDBOX_SPEC_KIND
export SANDBOX_PROVIDER=runtime_api
export SANDBOX_REMOTE_RUNTIME_API_URL=https://runtime-api.example.internal
export SANDBOX_API_KEY='<runtime-api-access-key>'
export SANDBOX_DEFAULT_TEMPLATE=python-v1
export SANDBOX_TEMPLATES='[
  {"id":"python-v1","config_name":"enterprise-python"}
]'
export OH_WEB_URL=https://openhands.example.com
export OH_SANDBOX_CALLBACK_URL=https://app-internal.example.com
```

Create a native warm-runtime configuration named `enterprise-python` using your
Runtime API deployment's existing administration mechanism. The app resolves this
name through `/api/warm-runtime-configs` and launches its complete image, command,
environment, working directory, and security settings. Two templates can use the
same image with different native configurations. The app caches native lookups
for 60 seconds.

Use the Agent Server image matching your app server's bundled SDK, or a custom
image built from that compatible release. The following command resolves the
configured bundled image without hardcoding a version. Run it from the application
checkout or its Python environment:

```sh
export TEMPLATE_AGENT_IMAGE="$(uv run --frozen python -c \
  'from openhands.app_server.sandbox.sandbox_spec_service import get_agent_server_image; print(get_agent_server_image())')"
```

For a native template that uses deferred initialization, set the same static
bootstrap key in its actual startup `OH_SECRET_KEY` and
`OH_SESSION_API_KEYS_0`, and set `OH_DEFERRED_INIT=true`. The following creates a
complete native configuration fragment to supply through your existing Runtime
API administration process:

```sh
export NATIVE_TEMPLATE_INIT_KEY='<static-key-from-your-secret-manager>'
umask 077
uv run --frozen python - <<'PY' > native-runtime-template.json
import json
import os

key = os.environ['NATIVE_TEMPLATE_INIT_KEY']
print(json.dumps({
    'name': 'enterprise-python',
    'image': os.environ['TEMPLATE_AGENT_IMAGE'],
    'command': ['/usr/local/bin/openhands-agent-server', '--port', '60000'],
    'working_dir': '/workspace/project',
    'environment': {
        'OH_DEFERRED_INIT': 'true',
        'OH_SECRET_KEY': key,
        'OH_SESSION_API_KEYS_0': key,
        'OH_CONVERSATIONS_PATH': '/workspace/conversations',
        'OH_BASH_EVENTS_DIR': '/workspace/bash_events',
        'OH_VSCODE_PORT': '60001',
        'OH_ENABLE_VNC': '0',
        'OPENVSCODE_SERVER_ROOT': '/openhands/.openvscode-server',
    },
    'run_as_user': 10001,
    'run_as_group': 10001,
    'fs_group': 10001,
}, indent=2))
PY
```

Supply the key through the provider's native secret references when supported.
The generated file contains that key; keep it in your deployment's secret storage.
Adapt the security IDs to your image. UID and GID zero are preserved when explicitly
configured.

Runtime API performs its own native activation and resume-key rotation. The app
does not need the native bootstrap key and does not initialize an already active
runtime again. An optional template `init_api_key_env` is validated as a reference
to a nonempty app environment variable. It does not inject that key into a native
template or alter activation; the native template must already contain its
bootstrap key. Existing Runtime API presets remain supported without converting
them to deferred initialization.

## Managed Docker

Use a PostgreSQL connection directly or through a pooler in session mode. Managed
Docker uses session advisory locks, so a transaction-mode pooler is unsupported.
Keep the database and app encryption keys durable. All app workers must share the
same `JWT_SECRET`, and it must remain available after restarts to decrypt stored
sandbox credentials. Existing keyring deployments can instead retain and share
their configured `JwtService` encryption keys.
All workers sharing this inventory must also use the same Docker daemon and
`DOCKER_HOST`; per-sandbox daemon routing is not implemented.

The Docker daemon must be reachable through the standard Docker client settings,
including `DOCKER_HOST` and Docker TLS variables when required. The app creates a
separate owned volume for each sandbox and publishes daemon-assigned host ports.
Each sandbox keeps its settings, secrets, profiles, conversation state, bash
history, worktrees, and project files under `workspace_mount_path`.

### App server running on the Docker host

This example assumes the browser and app server use the same host. The app server
listens on a container-reachable interface at port 3000, and the frontend runs at
`http://localhost:3000`. Adjust those addresses to your deployment.

```sh
unset OH_SANDBOX_KIND OH_SANDBOX_SPEC_KIND
export SANDBOX_PROVIDER=docker
export DOCKER_HOST=unix:///var/run/docker.sock
export JWT_SECRET='<durable-shared-app-encryption-key>'
export OH_WEB_URL=http://localhost:3000
export OH_PERMITTED_CORS_ORIGINS_0=http://localhost:3000
export SANDBOX_DEFAULT_TEMPLATE=python-v1
export TEMPLATE_AGENT_IMAGE="$(uv run --frozen python -c \
  'from openhands.app_server.sandbox.sandbox_spec_service import get_agent_server_image; print(get_agent_server_image())')"
export SANDBOX_TEMPLATES="$(uv run --frozen python - <<'PY'
import json
import os

print(json.dumps([{
    'id': 'python-v1',
    'image': os.environ['TEMPLATE_AGENT_IMAGE'],
    'command': ['--port', '8000'],
    'working_dir': '/workspace/project',
    'initial_env': {
        'OH_DEFERRED_INIT': 'true',
        'OH_PERSISTENCE_DIR': '/workspace/.openhands',
        'OH_CONVERSATIONS_PATH': '/workspace/conversations',
        'OH_BASH_EVENTS_DIR': '/workspace/bash_events',
        'OH_VSCODE_PORT': '8001',
        'OH_ENABLE_VNC': '0',
        'OPENVSCODE_SERVER_ROOT': '/openhands/.openvscode-server',
    },
    'docker': {
        'network': 'bridge',
        'bind_host': '127.0.0.1',
        'container_url_pattern': 'http://localhost:{port}',
        'webhook_url': 'http://host.docker.internal:3000/api/v1/webhooks',
        'extra_hosts': {'host.docker.internal': 'host-gateway'},
        'workspace_mount_path': '/workspace',
        'ports': {
            'AGENT_SERVER': 8000,
            'VSCODE': 8001,
            'WORKER_1': 8011,
            'WORKER_2': 8012,
        },
    },
}]))
PY
)"
```

The compatible Agent Server image already has the server executable as its
entrypoint, so its Docker `command` contains arguments such as `--port`.
An omitted command defaults to the configured `AGENT_SERVER` port. Explicit
commands must include a matching `--port` value.

The app generates a unique workspace encryption and initialization key for each
sandbox and sets it as `OH_SECRET_KEY` before the container process starts. It
also generates a separate startup service token for VS Code, sets the same token
in the singleton JSON list `OH_SESSION_API_KEYS` and in `OH_SESSION_API_KEYS_0`,
and enforces `OH_DEFERRED_INIT=true`. These values override
image defaults. Docker templates do not accept `init_api_key_env`, `OH_SECRET_KEY`,
or `OH_SESSION_API_KEYS` (including indexed variants); the app owns those values.
JSON strings are not interpolated as shell variables.

After creation, the app initializes the dormant server with the same workspace
key and a separate Agent Server API key. It publishes URLs and authentication
only after initialization and readiness succeed. Keep `OH_PERSISTENCE_DIR`,
`OH_CONVERSATIONS_PATH`, `OH_BASH_EVENTS_DIR`, `OH_CONVERSATION_WORKTREE_ROOT`, and
`OH_WORKSPACE_PATH` under the managed workspace mount. Omitted paths are filled
from that mount and the template working directory; `OH_PERSISTENCE_DIR` defaults
to `.openhands` inside the mount.

Optional Docker settings include `user` (for example `10001:10001`), `mem_limit`
(for example `4g`), `nano_cpus`, and `mounts` containing absolute `source`, `target`,
and `read_only` fields. Additional bind mounts must remain outside the managed
workspace mount. Their source paths belong to the operator and are never deleted
by sandbox cleanup. Host networking and container-shared networking are unsupported.

### App server running in a container

Publish on a Docker host address that both the app container and browser can
reach. For example, set these template options on an existing shared Docker
network:

```json
{
  "network": "openhands-network",
  "bind_host": "0.0.0.0",
  "container_url_pattern": "http://devbox.example.test:{port}",
  "webhook_url": "http://enterprise-backend:3000/api/v1/webhooks",
  "extra_hosts": {"host.docker.internal": "host-gateway"}
}
```

Join the app container to `openhands-network`, with `enterprise-backend` as its
network name. Configure `devbox.example.test` to resolve to the Docker host from
both the browser and app container. The callback must reach the app server's
`/api/v1/webhooks` route from inside the sandbox. On Linux, add the appropriate
`host.docker.internal:host-gateway` mapping to the app container when using that
hostname.

A remote Docker daemon requires the daemon host's reachable address in
`container_url_pattern`. The existing backend localhost rewrite can translate
`localhost` to `host.docker.internal` for a containerized backend, but it does not
make an otherwise unreachable published port accessible. The public sandbox URL
must work for both the browser and backend callers. Configure TLS, routing, and
firewall access for your deployment, and set `OH_WEB_URL` and
`OH_PERMITTED_CORS_ORIGINS_*` to the frontend origins that may access Agent Server.
The app combines those origins with any template `OH_ALLOW_CORS_ORIGINS` settings
and places them in the container startup environment, before Agent Server creates
its CORS middleware.

### HTTPS ingress for managed Docker

Set `docker.public_url_pattern` when a reverse proxy routes HTTPS hostnames to
managed container services:

```json
{
  "bind_host": "127.0.0.1",
  "public_url_pattern": "https://{container_port}-{resource_id}.sandboxes.example.com"
}
```

Both placeholders must appear exactly once in the first DNS label of an HTTPS
origin. Paths, queries, explicit ports, and format conversions are rejected.
`resource_id` is the app-generated resource identifier, independent of a requested
sandbox ID. `container_port` is the configured service port, not its host mapping.
The reverse proxy must limit upstreams to managed containers and permitted service
ports. It must support WebSockets and present a trusted certificate. The browser
and backend must both reach these public URLs.

The app persists the public pattern with each sandbox's launch settings and keeps
using it after a catalog change. Its default CSP permits the configured sandbox
HTTPS suffix. Leave the public pattern unset to retain the existing published-port
URLs. Set `OH_SANDBOX_CALLBACK_URL` to the app address reachable from sandboxes for
secrets and MCP. It defaults to `OH_WEB_URL`; `docker.webhook_url` can override the
webhook endpoint. The legacy Docker adapter retains its host-based webhook default
unless `OH_SANDBOX_CALLBACK_URL` is explicitly set.

## Select a template for a conversation

List available IDs with `GET /api/v1/sandbox-specs/search`. To select a template for
one conversation, send `sandbox_spec_id` in the conversation request:

```sh
curl --fail-with-body "$OPENHANDS_URL/api/v1/app-conversations" \
  -H "Authorization: Bearer $OPENHANDS_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"sandbox_spec_id":"python-v1"}'
```

The explicit template overrides user and system defaults. Grouping can reuse only
running sandboxes with that same template ID and available conversation capacity.
A parent conversation still supplies repository and other inherited settings, but
its sandbox is not inherited when an explicit template is selected. Do not combine
`sandbox_spec_id` with `sandbox_id` in one request.

Direct sandbox creation also accepts the stable ID:
`POST /api/v1/sandboxes?sandbox_spec_id=python-v1`. With no request override,
existing grouping and user-default fallback behavior remains in effect.

## Lifecycle and migration

Managed Docker supports manual pause, unpause, stop/start recovery, and deletion.
Pause preserves the initialized process and all its keys; unpause verifies
readiness and restores authentication. Restarting a stopped container preserves
the workspace encryption key and VS Code service token and initializes the new
process with a new Agent Server API key. An external automatic process restart is
reported as `STARTING`; call `POST /api/v1/sandboxes/{sandbox_id}/resume` to
initialize it. Inventory reads do not start or initialize containers. Provider
outages return `UNKNOWN` without active credentials or URLs; confirmed removal
returns `MISSING`.

The existing per-user sandbox count cap applies. `OH_SANDBOX_MAX_NUM_SANDBOXES`
defaults to 5. Scheduled idle pause, retention expiry, orphan sweeping, and
automatic reconciliation are future work for managed Docker. Runtime API continues to use its native lifecycle and housekeeping.

Conversation deletion archives the workspace according to the configured archive
policy before deleting an unreferenced sandbox. Direct sandbox deletion skips
archiving, as in the existing sandbox API contract. If cleanup fails, the record
remains available for a retry. Restore Docker connectivity and repeat
`DELETE /api/v1/sandboxes/{sandbox_id}`. Do not rely on a background worker to
finish failed cleanup in this phase.

Apply the application's normal database migrations before enabling managed Docker.
Both Enterprise and standalone app migration chains add managed inventory after
the Runtime API working-directory revision. Downgrading managed inventory preserves
the Runtime API working-directory snapshots. Runtime API retains its existing table and historical image-based spec lookup. New sandbox
records pin their working directory; older records continue their existing catalog
fallback behavior.

Leaving `SANDBOX_PROVIDER` unset preserves the legacy `RUNTIME` and `OH_*_KIND`
selection paths, including the existing local/process and Docker adapters. Explicit
`runtime_api` without a catalog uses the existing Runtime API default spec.
Configured catalogs require a valid default, and explicit Docker requires a
nonempty catalog. Remove `OH_SANDBOX_KIND` and `OH_SANDBOX_SPEC_KIND` when using the
new selector; conflicting configuration is rejected. Switching providers does not
transfer existing sandbox inventory between providers.
