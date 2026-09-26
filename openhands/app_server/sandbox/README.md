# Sandbox Management

Manages sandbox environments for secure agent execution within OpenHands.

## Overview

Since agents can do things that may harm your system, they are typically run inside a sandbox (like a Docker container). This module provides services for creating, managing, and monitoring these sandbox environments.

## Key Components

- **SandboxService**: Abstract service for sandbox lifecycle management
- **DockerSandboxService**: Docker-based sandbox implementation
- **RemoteSandboxService**: Runtime-API-based sandbox implementation
- **E2BSandboxService**: E2B microVM-based sandbox implementation
- **K8sAgentSandboxService**: Kubernetes sandboxes claimed from an agent-sandbox warm pool
- **ProcessSandboxService**: Local process-based sandbox implementation
- **SandboxSpecService**: Manages sandbox specifications and templates
- **SandboxRouter**: FastAPI router for sandbox endpoints
- **sandbox_store**: The sandbox table (`v1_remote_sandbox`), which records who
  owns each sandbox for every backend, and the helper that scopes reads to the
  caller.

## Features

- Secure containerized execution environments
- Sandbox lifecycle management (create, start, stop, destroy)
- Multiple sandbox backend support (Docker, Remote, E2B, Kubernetes agent-sandbox, Local)
- User-scoped sandbox access control

## E2B backend

`E2BSandboxService` runs each sandbox as an E2B Firecracker microVM. Select it
with `RUNTIME=e2b`, which also selects `E2BSandboxSpecService`.

| Variable | Purpose |
| --- | --- |
| `E2B_API_KEY` | E2B API key |
| `E2B_DOMAIN` | Domain sandbox ports are exposed under, as `https://{port}-{sandbox_id}.{domain}` |
| `E2B_API_URL` | Control plane URL. Self hosted clusters only; otherwise `https://api.{domain}` |
| `E2B_TEMPLATE` | Template to start sandboxes from. Defaults to `openhands-agent-server` |
| `E2B_INIT_API_KEY` | **Required.** The `OH_SECRET_KEY` baked into the template, sent as `X-Init-API-Key` on `POST /api/init` |

Build the template first — sandboxes are created from it, not from an image
reference:

```bash
uv run scripts/e2b/build_template.py
```

The script generates an init key, bakes it into the template as
`OH_SECRET_KEY`, and prints it at the end as `E2B_INIT_API_KEY=...`. **That
value must be given to the app server** as `E2B_INIT_API_KEY`. The two are one
key seen from two sides: the template boots its agent server holding it, and
the app server has to present the same value to claim a sandbox. Without it
`start_sandbox` fails before creating anything; with the wrong value the agent
server answers `POST /api/init` with a 401. Pass `--init-api-key` to rebuild a
template without rotating its key.

The template is built with 2 vCPU and 2048 MB, which is what this image has
been exercised at. `--cpu-count` and `--memory-mb` change that, but a cluster
node has to be able to fit the result: E2B rejects a `create()` it cannot
place, with `Failed to place sandbox: sandbox creation failed on N node(s)`.
That failure is permanent rather than transient — the template builds and
lists as `ready`, and then every sandbox fails — so size the template against
the nodes you actually have.

Ownership, spec identity and the session API key live in the app's sandbox
table (see `sandbox_store`), shared with the other backends. The E2B metadata
(`oh_managed`, `oh_user_id`, `oh_spec_id`) tags managed sandboxes so that one
with no row can be found. A row whose sandbox
E2B no longer has reports `MISSING`, which is what archives the conversation —
E2B's own `SandboxState` has no state for a reaped sandbox.

### Known limitations

Headless flows — integration-triggered runs with no browser attached — are not
fully supported by this backend in v1.

**`OH_WEB_URL` must be publicly reachable.** The agent server posts events back
over a webhook, and unlike the remote runtime backend there is no polling
fallback. A sandbox started against a localhost app server runs, but its events
never arrive.

**A sandbox holds a one-hour lease by default.** `timeout_seconds` defaults to
3600. The ceiling above that is set by the E2B plan — one hour on Hobby, 24
hours on Pro — and on a self hosted cluster by the operator, so a rejection
saying `Timeout cannot be greater than 1 hours` is that cluster's configuration
rather than an E2B limit. On expiry the sandbox pauses rather than being
destroyed (`on_timeout: pause` with `auto_resume: true`), parking as a memory
snapshot with its filesystem and processes intact. An interactive session
self-heals: the browser's next request wakes the sandbox in about 0.3 s and the
conversation carries on. A headless run has no such request, so it can stall at
the lease's expiry with nothing to resume it. The fix for a follow-up is to
renew the lease when the sandbox delivers a webhook — during a headless run
that is the one signal that tracks actual activity.

## Kubernetes agent-sandbox backend

`K8sAgentSandboxService` claims each sandbox from a
[kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox)
warm pool (v1.0 or later, with the extensions), through agent-sandbox's Python
SDK (`k8s-agent-sandbox`). Select it with `RUNTIME=k8s-agent-sandbox`, which
also selects `K8sAgentSandboxSpecService`.

| Variable | Purpose |
| --- | --- |
| `AGENT_SANDBOX_NAMESPACE` | Namespace of the warm pool. The app makes its claims there |
| `AGENT_SANDBOX_WARM_POOL` | The `SandboxWarmPool` to claim from. Defaults to `openhands-agent-server` |
| `AGENT_SANDBOX_INIT_API_KEY` | **Required.** The `OH_SECRET_KEY` in the pool's `SandboxTemplate`, sent as `X-Init-API-Key` on `POST /api/init` |
| `AGENT_SANDBOX_ROUTER_URL` | **Required.** Public URL of the agent-sandbox router's path prefix, for example `https://openhands.example.com/sandbox-router` |
| `AGENT_SANDBOX_WEBHOOK_BASE_URL` | The app URL pods post events to, when it is not `OH_WEB_URL` |

In the cluster, the app uses its pod's ServiceAccount. Outside it, the app
uses the current context of `KUBECONFIG` (or `~/.kube/config`).

The operator creates everything up front, and the app only creates and deletes
`SandboxClaim`s. `scripts/k8s_agent_sandbox/` has example manifests for all of
it. Set the app's host in `40-ingress.yaml` and the app's ServiceAccount in
`50-app-rbac.yaml`, then:

```bash
kubectl apply --server-side -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v1.0.3/sandbox-with-extensions.yaml
kubectl apply -f scripts/k8s_agent_sandbox/00-namespace.yaml
kubectl -n openhands-sandboxes create secret generic agent-server-init \
  --from-literal=init-api-key="$(openssl rand -hex 32)"
kubectl apply -f scripts/k8s_agent_sandbox/
```

Give the app the Secret's value as `AGENT_SANDBOX_INIT_API_KEY`. The manifests
set up:

- A `SandboxTemplate` running the agent server image with `OH_DEFERRED_INIT=1`
  and `OH_SECRET_KEY` from that Secret, `service: true`, a readiness probe on
  `/ready`, and a volume at `/workspace`. A claim that sets env or volumes is
  cold-started instead of served from the pool, so the app sets neither.
  `envVarsInjectionPolicy` and `volumeClaimTemplatesPolicy` are `Disallowed`,
  so a claim that does fails loudly.
- In that template, `OH_VSCODE_BASE_PATH` set to VSCode's router path,
  `/sandbox-router/$(POD_NAMESPACE)/$(POD_NAME)/8001`, with `POD_NAME` and
  `POD_NAMESPACE` from the downward API. The router strips the path before
  forwarding, and openvscode-server answers with or without it, but it needs
  the path to write its own links.
- A `SandboxWarmPool` on that template. Its name is the sandbox spec id.
- agent-sandbox's router, run with `--path-routing-prefix=/sandbox-router`. A
  browser cannot set the router's `X-Sandbox-*` headers on a WebSocket, so the
  app hands out `{router}/{namespace}/{sandbox}/{port}` URLs. Both frontends
  accept an agent server URL with a path. The prefix has to match in the
  router's flag, `AGENT_SANDBOX_ROUTER_URL` and `OH_VSCODE_BASE_PATH`.
- An Ingress that serves the router on the app's own host at
  `/sandbox-router`, keeping the path. On the app's origin, the browser needs
  no CORS to reach the agent server.
- A Role for the app: `create`, `get`, `watch` and `delete` on
  `sandboxclaims`, and `get`, `watch` and `patch` on `sandboxes`.
- A network policy on the template that admits the router on ports 8000
  (agent server), 8001 (VSCode), 8011 and 8012 (workers). agent-sandbox's
  default policy also blocks cluster DNS and every private address. The
  example allows DNS and public addresses. Pods must reach the app's URL and
  the LLM base URL, so add a rule for either one if it is private.

`start_sandbox` creates a claim on the pool, waits for its Ready condition, and
then completes the same `POST /api/init` handshake as E2B: the pod booted
before any user existed, so everything per user reaches it that way. The claim
name is the sandbox id. Ownership and the session API key live in the sandbox
table. `pause_sandbox` sets the Sandbox's `operatingMode` to `Suspended`, which
deletes the pod and keeps its volume and Service. `resume_sandbox` sets it back
to `Running` and repeats the handshake on the new pod with the stored key. The
SDK has no suspend or resume, so those two patch the Sandbox directly.
The agent server's secret key is the session key, so the secrets it persisted
on the volume still decrypt.

### Known limitations

- A pod that restarts on its own, after a crash or an eviction, boots dormant.
  The app does not initialize it again until the sandbox is paused and resumed.
- As on E2B, pods must be able to reach `OH_WEB_URL` (or
  `AGENT_SANDBOX_WEBHOOK_BASE_URL`), because there is no polling fallback.
