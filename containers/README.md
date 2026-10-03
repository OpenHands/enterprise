# Docker Containers

`app/Dockerfile` builds the enterprise-server image — the only image this repo
publishes, built and pushed in GitHub Actions by the `ghcr-build.yml` workflow.
It installs the Python dependencies with uv from `uv.lock`, builds the frontend,
and copies the repository's Python tree (`openhands/` plus the SaaS modules
`server/`, `storage/`, `integrations/`, `sync/`, ...) into `/app`.
`dev/` holds the local development container.

## Building Manually

```bash
# the enterprise server, as CI builds it
docker build -f containers/app/Dockerfile -t enterprise-server .
```
