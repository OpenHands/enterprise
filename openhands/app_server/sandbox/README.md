# Sandbox Management

Manages sandbox environments for secure agent execution within OpenHands.

## Overview

Since agents can do things that may harm your system, they are typically run inside a sandbox (like a Docker container). This module provides services for creating, managing, and monitoring these sandbox environments.

## Key Components

- **SandboxService**: Abstract service for sandbox lifecycle management
- **DockerSandboxService**: Docker-based sandbox implementation
- **SandboxSpecService**: Manages sandbox specifications and templates
- **SandboxRouter**: FastAPI router for sandbox endpoints

## Features

- Secure containerized execution environments
- Sandbox lifecycle management (create, start, stop, destroy)
- Multiple sandbox backend support (Docker, Remote, Local)
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
| `E2B_INIT_API_KEY` | The `OH_SECRET_KEY` baked into the template, sent as `X-Init-API-Key` on `POST /api/init` |

Build the template first — sandboxes are created from it, not from an image
reference:

```bash
uv run scripts/e2b/build_template.py
```

The script prints the `E2B_INIT_API_KEY` to configure the app server with.

The backend keeps no state of its own. Ownership and spec identity live in E2B
sandbox metadata (`oh_managed`, `oh_user_id`, `oh_spec_id`), and each sandbox's
session API key is derived from its id with the app server's encryption key, so
both survive an app server restart with nothing persisted.

`OH_WEB_URL` must be publicly reachable: the agent server posts events back over
a webhook, and unlike the remote runtime backend there is no polling fallback.
