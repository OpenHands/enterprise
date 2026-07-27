# Docker Containers

`app/Dockerfile` builds the enterprise-server image — the only image this repo
publishes, built and pushed in GitHub Actions by the `ghcr-build.yml` workflow.
Its `openhands-app` stage is the plain app image the enterprise layers sit on.
`dev/` holds the local development container.

## Building Manually

```bash
# the enterprise server, as CI builds it
docker build -f containers/app/Dockerfile -t enterprise-server .

# just the app underneath it
docker build -f containers/app/Dockerfile --target openhands-app -t openhands .
```
