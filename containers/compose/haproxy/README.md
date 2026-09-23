# HAProxy supplied-certificate overlay

Follow the common README. Set `TLS_CERT_DIR` to an absolute directory containing
`bundle.pem`: leaf/intermediate certificates followed by the private key. Restrict
access to that file. Its certificate must cover the public hostname/IP; verify
TLS using its trusted CA. This POC publishes loopback HTTPS on 8443.

From `containers/compose`:

```sh
docker compose --env-file .env -f compose.yaml -f haproxy.yaml config --quiet
docker compose --env-file .env -f compose.yaml -f haproxy.yaml up -d
docker compose --env-file .env -f compose.yaml -f haproxy.yaml exec proxy haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg
docker compose --env-file .env -f compose.yaml -f haproxy.yaml kill -s USR2 proxy
docker compose --env-file .env -f compose.yaml -f haproxy.yaml down
```

The foreground master-worker command supports the USR2 reload above. Validate
first; replace the certificate bundle atomically in its mounted directory.
Public ACME is outside this supplied-certificate test.

Automation has 16 active request slots and eight queued requests, with a 100ms
queue timeout and 15s server inactivity timeout. Excess work receives 503. Retries
are disabled to avoid duplicate writes. Enterprise/server and tunnel inactivity
timeouts are one hour. These are not total request deadlines. Queue semantics
differ from Caddy and nginx; compare outcomes, not just setting names.

Both backends use active TCP health checks (`check inter 500ms fall 2 rise 2`).
Once a backend is marked down, HAProxy returns an immediate, cleanly delivered
`503` (termination flag `SC`) instead of attempting a connection. `option httplog`
records per-request termination flags for diagnostics.

Known behavior: in the brief window after a backend disappears but before health
checks mark it down, a request that reuses an idle keep-alive client connection
can be answered with a connection close (no HTTP status) rather than a `503`
(termination flag `sC`). This is standard HTTP keep-alive behavior: HAProxy drops
the reused connection to signal the client to reconnect, and a spec-compliant
client retries the idempotent request on a fresh connection and then receives the
`503`. Enterprise traffic is unaffected. Clients that do not retry idle-connection
closes may observe a transport error during that sub-second window.

Docker DNS handles backend replacement. Switch to another candidate using the
same project/base and `up -d --no-deps proxy` with its overlay. The reserved proxy
IP keeps app trust settings stable. Single-proxy replacement interrupts traffic.
Authenticated execution, broader faults, reload/rotation on real workloads and
soak testing remain necessary before treating this as a production profile.
