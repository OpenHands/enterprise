# Integrations Hub API map (enterprise FE ↔ in-process Hub FastAPI)

Source of truth: vendored Hub package [`enterprise/integrations_hub/`](../enterprise/integrations_hub/)
(from the former standalone `integrations-hub` FastAPI).

Public mount: `/api/integrations-hub` (in-process on `saas_server` when
`ENABLE_INTEGRATIONS_HUB` is on). Hub rewrites that public prefix to internal
`/api/...` routes via `INTHUB_API_ROOT_PATH`.

## Auth

| Concern | Behavior |
| --- | --- |
| Dashboard session | Hub validates OpenHands session cookie via `GET <INTHUB_OPENHANDS_BASE_URL>/api/v1/users/me` (same process; use same-origin / loopback base URL) |
| Flag | `ENABLE_INTEGRATIONS_HUB` — Hub is **not mounted** when off (404) |
| Admin | Hub: org `owner`/`admin` role (or email allowlist for personal/null org). Enterprise Settings already gates admin routes via `ADMIN_ONLY_SETTINGS_PATHS` |

## Database (shared with OHE)

Hub uses the **same Postgres** as the rest of OpenHands Enterprise:

1. Prefer `INTHUB_POSTGRES_URL` if set (tests / explicit override)
2. Otherwise build from `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASS`

Alembic for Hub lives under `enterprise/integrations_hub/alembic/` and records
revisions in `alembic_version_integrations_hub` so it does not collide with
enterprise SaaS `alembic_version`. Hub tables (`owner_integrations`, etc.) live
in the shared database.

```bash
cd enterprise
# Uses DB_* (or INTHUB_POSTGRES_URL override)
poetry run alembic -c integrations_hub/alembic.ini upgrade head
```

## Mounting

| Piece | Location |
| --- | --- |
| Hub FastAPI app | `integrations_hub.main:app` |
| Mount helper | `integrations_hub.mount.mount_integrations_hub` |
| Wired from | [`enterprise/saas_server.py`](../enterprise/saas_server.py) when `ENABLE_INTEGRATIONS_HUB` |

The old HTTP reverse proxy (`INTEGRATIONS_HUB_BACKEND_URL`) was removed.

## ViewModel → endpoints

### Reads

| ViewModel field | Hub internal | Public path |
| --- | --- | --- |
| `integrations` | `GET /api/integrations` | `/api/integrations-hub/integrations` |
| `catalogIntegrations` | `GET /api/integrations?filter=enabled` | same + query |
| `requestableCatalog` | `GET /api/integrations?filter=registerable` | same |
| `approvals` | `GET /api/case-by-case-approvals` | `/api/integrations-hub/case-by-case-approvals` |
| `userRequests` | `GET /api/admin/integration-requests` | `/api/integrations-hub/admin/integration-requests` |
| `apiKeys` | `GET /api/user/key` + `GET /api/user/agent-key` | `/api/integrations-hub/user/key` etc. |
| `permissionProfiles` | `GET /api/permission-profiles` | `/api/integrations-hub/permission-profiles` |
| `overviewUsers` / `duplicateGroups` | `GET /api/admin/overview` | `/api/integrations-hub/admin/overview` |

### Writes

| ViewModel method | Hub endpoint |
| --- | --- |
| `connect(slug)` | OAuth: `GET /api/oauth/{provider}/start`; API key: `POST /api/integrations` after discover |
| `disconnect(slug)` | `DELETE /api/integrations/{integrationKey}` |
| `toggleEnabled(slug)` | `POST /api/integrations/{integrationKey}/toggle` |
| `updateToolAccess` | `PATCH /api/integrations/{integrationKey}/tools/{toolName}` |
| `requestIntegration` | `POST /api/integrations/requests` |
| `decideApprovals` | `POST /api/case-by-case-approvals/{id}/approve\|reject` |
| `dismissUserRequest` | `POST /api/admin/integration-requests/{id}/dismiss` |
| `fulfillUserRequest` | `POST /api/admin/integration-requests/{id}/add` |
| `registerCustomMcp` | `POST /api/admin/modify-integrations` |
| `addPermissionProfile` | `POST /api/permission-profiles` |
| `savePermissionProfileSnapshot` | `PATCH /api/permission-profiles/{id}` |
| `setDefaultPermissionProfile` | `POST /api/permission-profiles/{id}/load` |
| `deletePermissionProfile` | `DELETE /api/permission-profiles/{id}` |
| `disableUnusedTools` | `POST /api/integrations/disable-unused` |

## Type adapters

| Hub (SOT) | Enterprise FE |
| --- | --- |
| `IntegrationSpec.key` | `HubIntegration.slug` |
| `IntegrationSpec` tools dict | `HubIntegration.tools[]` |
| `ToolSpec.accessMode` / `lastInvokedAt` | `HubTool.accessMode` / `lastUsedAt` |
| `AccessRequest` | `HubApproval` |
| `IntegrationRequestRecord` | `HubUserRequest` |
| `PermissionProfile` | `HubPermissionProfile` |
| `ManagedConnector.slug` | catalog `HubIntegration.slug` |

## Local / deploy env

| Env | Purpose |
| --- | --- |
| `ENABLE_INTEGRATIONS_HUB=true\|1` | Show Hub UI + mount in-process FastAPI |
| `INTHUB_API_ROOT_PATH` | Defaults to `/api/integrations-hub` when mounted |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASS` | Shared OHE Postgres (Hub default) |
| `INTHUB_POSTGRES_URL` | Optional override of the shared DB URL |
| `INTHUB_OPENHANDS_BASE_URL` | Origin for session cookie validation (`/api/v1/users/me`) |
| `INTHUB_CREDENTIAL_ENCRYPTION_KEY` | Required for OAuth / stored credentials |
| `INTHUB_CRON_SECRET` | Expire-grants cron auth |
| `INTHUB_APP_ADMIN_EMAILS` | Admin allowlist for personal/null org |

### Run

With the flag on, `make start-backend` / `saas_server` serves Hub under
`/api/integrations-hub/*` in the same process and same Postgres as OHE — no
separate Hub service or Hub database.
