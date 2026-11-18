# Local HTTP reproduction

Temporary review artifact, not application code. Written by OpenHands AI on behalf of Graham Neubig.

From repository root (SDK/agent-server 1.62.0):

```sh
uv run python .pr/automation-telemetry/reproduce.py --baseline --output /tmp/attribution-before --expect false
uv run python .pr/automation-telemetry/reproduce.py --output /tmp/attribution-after --expect true
```

Use fresh output paths for each run. The baseline imports the byte-for-byte service from `b0d455745` using `git show`; it does not edit the working source. Current service is imported for the second command.

Real TCP HTTP: enterprise app-conversation router -> actual service and request builder -> actual agent-server create endpoint -> real classifier/subscriber/event factory/buffered sink/HTTP exporter -> local collector. Captures requests, app records, runtime responses, and telemetry as JSON. Expected before: 200/READY, app tags present, runtime tags empty, `is_automation=false`. Expected after: 200/READY, supplied runtime tags, `is_automation=true`. Exactly one creation event and zero LLM HTTP calls in each run.

Boundaries: synthetic user/auth/settings and already-running local sandbox, no-op provisioning setup, JSON app record persistence rather than PostgreSQL, empty pending-message store. Three uvicorn apps in one process on OS-assigned loopback TCP ports. Actual SDK storage and telemetry path, not mocked classification. This does not test Keycloak, production middleware, runtime-api allocation, PostgreSQL semantics, automation scheduler, or PostHog vendor ingestion. No initial message; explicit title; no tools/plugins/marketplaces/MCP/secrets; LLM URL points to a local endpoint that fails if called. Non-loopback socket connections are blocked. Per-run persistence is under output directory. All servers shut down in finally.
