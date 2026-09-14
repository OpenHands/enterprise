# Configure sandbox providers and templates

Set `SANDBOX_PROVIDER=runtime_api` to configure a Runtime API catalog on the app
server. Define stable IDs in `SANDBOX_TEMPLATES`, which is one JSON array environment
value, and select the default with `SANDBOX_DEFAULT_TEMPLATE`. Entries inherit
`runtime_api` when `provider` is omitted. The catalog loads at app startup.

Use a new template ID when changing an image or workspace layout, such as
`python-v2`. Template commands, startup environment values, and credentials stay
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

## Callbacks and existing deployments

Set `OH_SANDBOX_CALLBACK_URL` to the app address reachable from sandboxes for
webhooks, secret lookups, and MCP. It defaults to `OH_WEB_URL`. Public browser links
and CORS continue to use `OH_WEB_URL`. The legacy Docker adapter keeps its
`http://host.docker.internal:<host_port>` webhook default unless
`OH_SANDBOX_CALLBACK_URL` is explicitly set.

Leaving `SANDBOX_PROVIDER` unset preserves the existing `RUNTIME` and `OH_*_KIND`
selection paths. Existing `RUNTIME=remote` deployments continue to use Runtime API;
the standalone default without `RUNTIME` remains the legacy Docker adapter. Explicit
`runtime_api` without a catalog uses the existing Runtime API default spec. A
populated catalog requires a valid default. Remove `OH_SANDBOX_KIND` and
`OH_SANDBOX_SPEC_KIND` when using the selector; conflicts are rejected.

Apply the application's normal database migrations before starting the updated
app. New Runtime API records pin their working directory. Historical records keep
a nullable snapshot and resolve the existing catalog when needed. A provider outage
returns `UNKNOWN` without active credentials or URLs; confirmed removal returns
`MISSING`. The UI preserves the conversation and polls for recovery while the
provider is unavailable.
