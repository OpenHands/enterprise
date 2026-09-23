# Enterprise + automation: Compose installation POC

This is the shared **real application** stack for OHE-3340, not the controlled
FastAPI fixtures in `tests/integration/proxy`. It is a work in progress, not a
supported installer or a production recommendation. Candidate branches add small
overlays; the application stack stays the same.

## Pinned sources

- Enterprise: `8fd5a354810914c92610c3148906c85715056459`.
- Automation: `49fe6639aa328cd5c24a6c869712445bebf9bacc`.
- Published images and database/cache dependencies are pinned by digest in YAML.
- Automation's published image is AMD64-only. ARM Docker hosts use emulation;
  these installation trials cannot establish native application performance.

## Before startup

Use Docker Engine and Compose supporting `service_completed_successfully`.
Allow approximately 6 GiB for this POC's configured container memory limits,
plus Docker/OS headroom and any identity or execution services. This is a test
allocation, not a measured minimum installation requirement.

Copy `.env.example` to `.env`, set distinct database passwords and unique shared
service/webhook/KV secrets, and restrict its permissions to 0600. Configure a
non-production Keycloak realm/client to exercise login. Backend and browser URLs
may differ, but must describe the same realm and configured issuer. Execution
also needs an actual sandbox provider and LLM configuration. No production
credentials or databases are needed for the POC. Never dump a rendered Compose
configuration with real secrets; validate with `config --quiet`.

Automation deliberately uses Enterprise authentication. Setting
`AUTOMATION_AGENT_SERVER_URL` would switch it into local single-user mode and
would not test the Enterprise identity integration.

## Common-stack commands

From this directory:

```sh
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml up -d
docker compose --env-file .env -f compose.yaml ps
docker compose --env-file .env -f compose.yaml down
```

The common stack intentionally publishes no ports. Add a candidate overlay for
the single public HTTPS origin. Use the same project name, environment file and
base file for every candidate. Keep the supplied certificate directory outside
version control. Only trust forwarded headers from the proxy's private address
(`PROXY_TRUSTED_IPS`); refresh that value if its address changes, or reserve an
address in a deployment-specific network. Do not expose app/database ports.

Each PostgreSQL database has a distinct service and volume. One-shot migrations
must finish successfully before the corresponding app starts. Enterprise starts
independently of automation and uses Redis for readiness. A narrowly scoped
initialization container assigns the data volume roots to image UID 42420.
The image's `NO_SETUP=true` path permits non-root Enterprise startup. Its legacy
configuration validator also requires a nonempty PostHog key: the deliberately
invalid `disabled-self-hosted` sentinel satisfies validation. Both backend and
frontend disable analytics under `OH_DEPLOYMENT_MODE=self_hosted`; no real
telemetry credential is supplied.

## Persistence and recovery

`down` preserves data. `down --volumes` permanently deletes this project's
databases and application storage: use only to reset a disposable trial.
`restart: unless-stopped` handles process exit; health checks **do not** restart
a hung process. Recovery for a hung automation server is an operator
`docker compose ... restart automation`. Test pausing/unpausing separately from
process crashes. Back up both databases and application volumes, plus secret and
certificate material. Redis is configured as a non-persistent cache; confirm
that this is sufficient for the intended production workflows before promotion.

For upgrades, back up databases, update digest pins, run migrations, then recreate
apps. An image rollback is not automatically a database-schema rollback.

## Validation boundary

`tests/integration/compose_install/test_install_contract.py` checks the rendered
Compose model: no published backend ports, independent migration ordering,
distinct persistent databases, resource bounds and Enterprise authentication.
Run it with `--confcutdir=tests/integration/compose_install`.

Readiness verifies database/cache connectivity, not login, authorization,
automation execution, schema compatibility across releases, or user-facing
availability under failures. Those require the separate real-stack trials.
