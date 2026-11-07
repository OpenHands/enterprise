# Agent Canvas packaging

Agent Canvas (the `OpenHands/OpenHands` SPA) is served at `/canvas`. There are two ways to ship it, and the version a
deployment serves depends only on the mode it uses. Do not infer the hosted Canvas version from the embedded-build
default, or the other way round.

## Modes

| Install mode | Who serves `/canvas` | Artifact | Version selected by | Owner |
| --- | --- | --- | --- | --- |
| Helm / Replicated (cloud, staging, feature previews, self-hosted VM installs) | separate `agent-canvas` service behind an ingress rule for `/canvas` | `ghcr.io/openhands/agent-canvas:<tag>` | `image.tag` of the `agent-canvas` subchart in `OpenHands/OpenHands-Cloud` (`charts/openhands/charts/agent-canvas/values.yaml`); `replicated/openhands.yaml` does not override it | OpenHands-Cloud |
| `docker-compose.yml` | separate `canvas-server` service behind the Caddy proxy | `ghcr.io/openhands/agent-canvas:<tag>` | `AGENT_CANVAS_IMAGE`, default pinned in `docker-compose.yml` | this repo |
| `make run` / `make run-saas` / `AGENT_CANVAS=1 make build` | this app (Vite dev plugin, or the `/canvas` static mount in `saas_server.py` and `openhands/app_server/app.py`) | bundle built by `frontend/scripts/build-agent-canvas.sh` into `frontend/public/canvas` | `AGENT_CANVAS_REF`, default pinned in the script | this repo |
| `containers/app/Dockerfile` with `--build-arg AGENT_CANVAS=1` | this app (static mount) | same script, run inside the image build | `--build-arg AGENT_CANVAS_REF`, default pinned in the Dockerfile; `AGENT_CANVAS_SOURCE=none` clones it in the script, `AGENT_CANVAS_SOURCE=git` lets BuildKit fetch it | this repo |

The published `enterprise-server` image is built without `AGENT_CANVAS`, so it contains no embedded Canvas. The
deployments that run it rely on a separate Canvas service.

The pins in this repo (script default, Dockerfile `AGENT_CANVAS_REF`, Compose image tag) must name the same release.
`tests/unit/test_build_agent_canvas_script.py` checks that. They do not follow the Helm chart tag. A version
difference between the chart and these pins is a packaging difference, not a missing feature. Change the chart tag in
OpenHands-Cloud.

## Which build is served

Each embedded build writes `agent-canvas-build.json` next to its `index.html`, so a running app answers at
`/canvas/agent-canvas-build.json`:

```json
{"repository":"https://github.com/OpenHands/OpenHands.git","ref":"v1.35.0","commit":"<sha>","source":"clone","base_path":"/canvas","lock_to_cloud":null}
```

- `ref` is the requested ref, `commit` the resolved commit. `commit` is `null` for `source: "prefetched"` (the
  Dockerfile `git` mode), because BuildKit fetches the tree without `.git`.
- `source` is `clone`, `cached-clone` (checkout in `frontend/.cache/agent-canvas` reused for the same ref) or
  `prefetched`.

`saas_server.py` logs the served build at startup, e.g. `Serving Agent Canvas ref=v1.35.0 commit=<sha> at /canvas
from ./frontend/build/canvas`, or `No Agent Canvas bundle at ...` when it does not serve `/canvas`.

The separate `agent-canvas` image does not contain this file; its static server answers the path with the Canvas
`index.html`. For those modes the version is the image tag of the running pod or container
(`kubectl get deploy -o jsonpath='{..image}'`, `docker compose images canvas-server`).

## Build failure

`make build-agent-canvas`, `make run*` and the Dockerfile treat a failed Canvas build as non-fatal. The script then
prints which of these is true:

- `no bundle is staged, so this app will not serve /canvas`: `/canvas` falls through to the OSS frontend, which reloads
  the page once and then shows its generic error page with **Try again**. This is the supported absence behavior. A
  deployment that needs Canvas must use a mode with a separate Canvas service, or fix the build.
- `public/canvas still holds the previously staged bundle: {...}`: a bundle from an earlier successful run is still
  served, and its manifest names its ref.
