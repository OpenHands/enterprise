# Sandbox Management

Manages sandbox environments for secure agent execution within OpenHands.

## Overview

Since agents can do things that may harm your system, they are typically run inside a sandbox (like a Docker container). This module provides services for creating, managing, and monitoring these sandbox environments.

## Key Components

- **SandboxService**: Abstract service for sandbox lifecycle management
- **DockerSandboxService**: Docker-based sandbox implementation
- **RemoteSandboxService**: Runtime-API-based sandbox implementation
- **E2BSandboxService**: E2B microVM-based sandbox implementation
- **ProcessSandboxService**: Local process-based sandbox implementation
- **SandboxSpecService**: Manages sandbox specifications and templates
- **SandboxRouter**: FastAPI router for sandbox endpoints
- **sandbox_store**: The sandbox table (`v1_remote_sandbox`), which records who
  owns each sandbox for every backend, and the helper that scopes reads to the
  caller.

## Features

- Secure containerized execution environments
- Sandbox lifecycle management (create, start, stop, destroy)
- Multiple sandbox backend support (Docker, Remote, E2B, Local)
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
value must be given to the app server**, either as `E2B_INIT_API_KEY` or as
`OH_SANDBOX_SPEC_SPECS_0_INIT_API_KEY`. The two are one key seen from two
sides: the template boots its agent server holding it, and the app server has
to present the same value to claim a sandbox. Without it `start_sandbox` fails
before creating anything; with the wrong value the agent server answers `POST
/api/init` with a 401. Pass `--init-api-key` to rebuild a template without
rotating its key.

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
