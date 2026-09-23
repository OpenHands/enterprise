# Thin reverse-proxy conformance tests

One public HTTPS origin, two independent FastAPI transport fixtures, and the same
pytest assertions against Caddy OSS, nginx OSS, and HAProxy Community. This lives
in Enterprise because that repository owns the installation boundary. Automation
needs no changes, checkout, database, authentication provider, or application
startup. No Pact broker or Docker Compose installation is involved.

## Run

From the Enterprise repository, with its development environment installed and a
local Docker daemon running:

```sh
uv run --no-sync python -m pytest tests/integration/proxy \
  --confcutdir=tests/integration/proxy -q \
  --junitxml=proxy-results.xml
```

`--confcutdir` is required: Enterprise's root conftest starts PostgreSQL even for
otherwise database-free tests. Use `-k nginx` (or `caddy`, `haproxy`) to select one
candidate. The runner uses the existing locked pytest, Docker SDK, HTTPX,
cryptography, FastAPI, and websockets dependencies. Nothing changes `uv.lock`.
Initial execution needs network access to pull images and build the fixture.
Proxy/base images are digest-pinned; fixture direct Python packages are version
pinned, but their transitive pip dependencies are not fully locked. This is a
functional comparison, not a hermetic benchmark.

Only proxy HTTPS ports are published, on randomly assigned loopback ports. Each
candidate gets a unique Docker bridge, two fixture containers, and disposable
self-signed TLS material verified by the client. The suite removes its containers,
anonymous volumes, and networks in fixture teardown, including setup failures.
Docker image/build caches remain for subsequent runs. A forcibly killed Python
process can leave resources labelled `openhands.proxy-test`; inspect that label
before removing only the resources from your interrupted run.

## What the contract establishes

- `/api/automation` and its slash-delimited descendants go to automation without
  prefix rewriting; Enterprise owns other API/UI paths. Query strings survive.
- Enterprise's actual `CHUNK_SIZE` and `MAX_CHUNKS` constants define the synthetic
  session-header size. Cookie/API-key/organization/authorization headers survive.
- TLS origin is preserved and spoofed forwarding headers are replaced.
- Exact raw signed bytes and success/rejection statuses survive transport.
- A 2 MiB binary upload survives with the same length and SHA-256 digest.
- Redirect targets and separate secure Set-Cookie headers survive.
- The first SSE event arrives before a three-second producer pause ends.
- A WebSocket exchanges messages before and after a configuration reload.
- Paused/unavailable automation does not stop a small Enterprise request sample;
  a proxy can start while automation is stopped and recover when it returns.

The fixture's HMAC, routes, credentials and cookies are synthetic. It is a
controlled peer, not a replacement implementation of either application.
Authentication, permissions, organization isolation, real tarball handling,
callback ownership, execution reachability, and database compatibility still
belong in each application's existing tests and a later real-stack smoke test.

## Configuration scope

The candidate files are **test configurations**, not supported install defaults.
Short upstream timeouts make fault tests bounded. Header limits reflect the
current Enterprise cookie budget. Production must choose request/stream timeouts,
upload limits, trusted forwarding peers, resource limits, certificate lifecycle,
logging and admission limits deliberately. The fixture trusts proxy headers from
its private test network; do not copy its wildcard trust into a public backend.

Passing these checks is not a throughput result, SLO, CPU/memory isolation proof,
production-readiness verdict, or a numerical ranking. Shared-host resource
exhaustion and automation-to-Enterprise authentication calls remain failure paths.

## Check that regressions are caught

After the clean suite passes, run this sequentially (never alongside the normal
suite, because it temporarily edits the checked-in configs):

```sh
uv run --no-sync python tests/integration/proxy/verify_regressions.py
```

It removes nginx's header budget, enables nginx response buffering, removes
Caddy's stream-close delay, and broadens HAProxy's route boundary, one at a time.
Each relevant test must report exactly one assertion failure and no setup errors.
The original file is restored in `finally`. This is a targeted check of four
known configuration regressions, not an exhaustive mutation score or application
code coverage measurement. SIGKILL during this script requires restoring the
one edited config manually.
