# Integrations Hub in enterprise — PR notes

## Summary

Brings Integrations Hub into OpenHands Enterprise as an **in-process** FastAPI
mount (not a separate Hub service), wires the existing Settings Hub UI to live
APIs behind `ENABLE_INTEGRATIONS_HUB`, and ships a **first-visit reconnect
modal** for the legacy Settings > Integrations cutover.

## Architecture

```
Browser  →  /api/integrations-hub/*  →  saas_server (same process)
                                         └─ integrations_hub FastAPI
                                              └─ shared OHE Postgres (DB_*)
```

- **Vendored package:** `enterprise/integrations_hub/` (from standalone
  `integrations-hub` backend)
- **Mount:** `integrations_hub.mount.mount_integrations_hub` when
  `ENABLE_INTEGRATIONS_HUB` is `true`/`1`
- **Removed:** HTTP reverse proxy + `INTEGRATIONS_HUB_BACKEND_URL`
- **FE DAL:** `frontend/src/api/integrations-hub/` + live TanStack provider
  (`use-integrations-hub-live`); stub kept for `VITE_MOCK_API=true`

## Shared database

Hub uses the **same Postgres as OHE**:

1. `INTHUB_POSTGRES_URL` if set (override / tests)
2. Else `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASS`

Alembic: `enterprise/integrations_hub/alembic/` with version table
`alembic_version_integrations_hub` (does not collide with SaaS
`alembic_version`).

```bash
cd enterprise
poetry run alembic -c integrations_hub/alembic.ini upgrade head
```

## Cutover reconnect modal

When Hub is enabled, first visit to personal Integrations shows
`IntegrationsHubCutoverModal`:

- Lists legacy git providers from `provider_tokens_set` that are not yet
  connected in Hub
- **Reconnect** opens the connect wizard when the Hub catalog has that slug
- Dismiss persists in `localStorage`
  (`integrations_hub_legacy_cutover_dismissed`)

## How to run locally

```bash
# Flag + shared DB (same as rest of OHE)
export ENABLE_INTEGRATIONS_HUB=1
export DB_HOST=... DB_NAME=... DB_USER=... DB_PASS=...
export INTHUB_OPENHANDS_BASE_URL=http://localhost:3000   # session cookie check
export INTHUB_CREDENTIAL_ENCRYPTION_KEY=...              # required for OAuth

cd enterprise
poetry run alembic -c integrations_hub/alembic.ini upgrade head
make start-backend   # or make run
```

Mock SaaS FE (stub Hub data, no real OAuth):

```bash
cd frontend && npm run dev:mock:saas
```

## Tests

```bash
# Hub backend (isolated confcutdir)
cd enterprise
PYTHONPATH=".:$PYTHONPATH" poetry run pytest tests/unit/integrations_hub/ \
  --confcutdir=tests/unit/integrations_hub -q

# FE Hub + cutover
cd frontend
npm run test -- __tests__/components/features/integrations-hub/ \
  __tests__/hooks/use-integrations-hub-cutover.test.tsx \
  __tests__/api/integrations-hub-adapters.test.ts
```

## First-party catalog additions

Enterprise supplements `openhands_extensions` with legacy Settings providers:

- `gitlab` (OAuth + PAT)
- `azure_devops` (PAT)
- `forgejo` (access token)
- `bitbucket_data_center` (PAT)
- `jira-dc` (PAT)

FE official catalog and Hub `default_managed_connectors()` both include these
so cutover **Reconnect** can target them.

## Resolvers in Integrations Admin

Admin Hub left nav includes a **Resolvers** tab. The page lists every
supported legacy provider with **Connected** chips when applicable. Row actions
go straight to the real next step (GitHub App install, GitLab/Azure OAuth,
Slack **Install**, webhook managers, Jira/Linear configure modals)—not a
wrapper modal that re-shows Connect/Configure.

## Follow-ups (not in this PR)

- In-process auth bridge (skip HTTP self-call to `/api/v1/users/me`)
- Fold Hub Alembic into enterprise migrations if desired
- Expand cutover detection beyond git `provider_tokens_set` (Slack/Jira Cloud)
- Wire full tool discovery / OAuth client setup for first-party HTTP connectors
- Archive standalone `integrations-hub` repo after cutover is proven
