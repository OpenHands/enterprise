# Thin reverse-proxy conformance tests

One public HTTPS origin, two independent FastAPI transport fixtures, and the same
pytest assertions against Caddy OSS, nginx OSS, and HAProxy Community. This lives
in Enterprise because that repository owns the installation boundary. Automation
needs no changes, checkout, database, authentication provider, or application
startup. No Pact broker or full product installation is involved. The original
transport suite uses Docker directly; the lifecycle suite uses Docker Compose.

## Run

From the Enterprise repository, with its development environment installed and a
local Docker daemon running:

```sh
uv run --no-sync python -m pytest tests/integration/proxy \
  --confcutdir=tests/integration/proxy -q \
  -o junit_family=xunit1 --junitxml=proxy-results.xml
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


## Compose lifecycle comparison

`test_compose.py` uses `compose.yaml` plus a generated candidate override. Every
case starts a fresh project with the proxy first, Enterprise second, and no
automation service. The runner supplies image/config/certificate paths; it does
not ask for production credentials. Run just these cases with:

```sh
uv run --no-sync python -m pytest tests/integration/proxy/test_compose.py \
  --confcutdir=tests/integration/proxy -q -o junit_family=xunit1 \
  --junitxml=compose-results.xml
```

Five lifecycle cases per candidate verify:

1. Startup with automation absent, later discovery, private backend ports and a
   loopback-only public proxy port.
2. Actual automation removal/recreation on a **different** IP, with the previous
   address deliberately occupied. Recovery must occur within ten seconds of the
   Compose start command, with no proxy restart.
3. Invalid configuration rejected by the vendor validator while the running
   configuration serves requests, followed by restoring/reloading valid config.
4. Supplied-certificate rotation: a new TLS connection verifies the replacement
   certificate while an established WebSocket continues to exchange messages.
5. Compose down/up with configuration and supplied certificate preserved.

JUnit records cached-image startup and replacement timings. These are small
local samples including Docker/Compose overhead, not performance rankings or
fresh-machine installation measurements. A reload can finish asynchronously;
the suite waits for a new response header on a new connection. Existing HTTP
connections can continue using an old worker during graceful reload.

Certificate generation happens only in the harness. It rotates a disposable
leaf certificate signed by a stable test CA, using atomic file replacement.
The probe requires five consecutive new-certificate observations within five
seconds after reload. TLS verification is never disabled. This does not test public ACME, private-CA
issuance, customer trust distribution or certificate-expiry monitoring.

For supplied certificates all candidates need one proxy service, routing config,
and certificate/key material (HAProxy reads a combined PEM). The Compose scaffold
is otherwise shared; this experiment does not establish a customer usability
winner. Human installation handoff, real application flows, customer-ingress
mode, offline distribution and sustained resource saturation remain separate
checks. These files are not a production Enterprise Compose installer.

## Opt-in measurements

`experiments.py` is deliberately outside normal pytest filename discovery. Run it
explicitly when collecting setup, exit, resource, and fault-containment evidence:

```sh
uv run --no-sync python -m pytest tests/integration/proxy/experiments.py \
  --confcutdir=tests/integration/proxy -q -o junit_family=xunit1 \
  --junitxml=measurements.xml
```

Use `-k modest_fault` for the primary self-hosted isolation experiment: Enterprise
stays at 20 requests/second and automation receives only 5 requests/second. The
sequence is healthy, hung, healthy with a backend cap, hung with that cap, crashed,
and recovered. Response waits are normalized to five seconds. The capped profiles
allow eight active automation slots. Caddy limits requests; nginx limits upstream
connections; HAProxy limits active HTTP requests and also allows eight queued
requests with a 100ms queue timeout. These are deliberately disclosed policy
differences, not equivalent queue implementations. These short-timeout profiles
are test settings, not a production streaming recommendation.

The proxy has a one-core/256 MiB cap; each fixture has half a core/256 MiB. The
footprint experiment separately samples idle/light Enterprise traffic and 32
concurrent healthy automation clients. Memory observations are end-of-window
samples, not measured peak memory. CPU is approximate usage of one core over the
sample window. Request counts, statuses, p50/p95/p99 and generator scheduling lag
are recorded in JUnit properties. No proxy assertion turns missing observations
into a success; functional assertion failures and measurement outcomes are
reported separately.

Setup uses three projects per candidate, already-cached images, and already-made
config/cert files. It measures time to both fixtures being reachable, not human
installation effort. Exit tests cover all six directions plus rollback, pin the
original public port, and assert unchanged backend IDs/start timestamps. The
continuous Enterprise probe runs approximately every 50ms with a 500ms timeout.
Its longest gap between successes is a sampled gap, not an exact outage duration.
A single proxy container replacement is expected to interrupt requests.
