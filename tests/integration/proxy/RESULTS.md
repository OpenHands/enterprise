# Local comparison results — 2026-09-23

**All three candidates passed the current transport and Compose lifecycle gates.**
This supports using any of them as a thin proxy between separate Enterprise and
automation services. It does not establish a production or installation-usability
winner.

| Candidate | Transport checks | Compose lifecycle checks | Different-IP recovery* | Certificate rotation* |
| --- | ---: | ---: | ---: | ---: |
| Caddy OSS 2.11.4 | 14/14 | 5/5 | 0.459 s | 0.642 s |
| nginx OSS 1.30.5 | 14/14 | 5/5 | 1.534 s | 0.643 s |
| HAProxy Community 3.4.4 | 14/14 | 5/5 | 0.446 s | 0.802 s |

Full run: **57 passed, no failures or skips, 84.58 seconds**. Docker Compose
5.1.2 / OrbStack, native ARM64 fixture and proxy containers, cached images. Image
digests are in `conftest.py` and `Dockerfile`.

\* Single local observations, including harness/Compose overhead. Rotation
includes certificate generation and five consecutive new-certificate probes.
These are not throughput or latency rankings. All candidates met the predeclared
ten-second replacement and five-second post-reload certificate-convergence
bounds. Automation's IP actually changed from `.4` to `.5`; the proxy's container
ID and start timestamp stayed unchanged.

## What the extra Compose checks established

- Proxy starts before either backend; Enterprise works while automation is absent.
- Automation becomes reachable when started and recovers after replacement onto
  a different IP, without restarting the proxy.
- Only the proxy publishes a loopback port; backend ports stay private.
- Vendor configuration validation rejects malformed input while the previous
  running config remains usable; restoring and reloading a valid file works.
- Supplied TLS leaf-certificate rotation preserves an established WebSocket and
  presents the replacement certificate to new clients, with verification on.
- Compose down/up preserves the supplied certificate and routing configuration.

## Findings and investigation limits

Use a directory mount for certificate material and replace files atomically
before a validated reload. nginx can inherit SSL objects when both modification
time and file index are unchanged; atomic replacement avoids relying on timestamp
resolution. [nginx documentation](https://nginx.org/en/docs/ngx_core_module.html#ssl_object_cache_inheritable).

Check new connections when confirming a graceful configuration reload. An
established HTTP connection can still use the old nginx worker. The certificate
probe now uses a stable test CA and waits for five consecutive observations of
the new leaf, rather than changing the client's root of trust mid-reload.

Initial lifecycle failures came from harness problems: reserving an address on
a container attached to Docker's `none` network, a purportedly invalid Caddy
config that was valid site syntax, checking old HTTP connections after reload,
and unrealistic certificate/trust replacement. Those are not candidate defects.

One later run encountered a nginx WebSocket handshake timeout before rotation.
An isolated rerun, the complete passing run, and **five further fresh-project
repeats** passed. No root cause was established; this remains an observed
intermittent test failure, not something to erase from the comparison.

## Decision supported by these tests

Keep all three eligible. Supplied-certificate installation has the same basic
shape for each: one proxy service, static routing config, certificate/key files,
and a validation/reload procedure. HAProxy uses a combined PEM. The same Compose
scaffold works for all three; candidate-specific config and startup commands are
injected by the harness.

A second engineer has not run an installation handoff. Fresh-machine setup,
public ACME, customer ingress terminating TLS, corporate proxies, air-gapped
installation, sustained load and actual Enterprise/automation application flows
remain untested. No real application credentials or databases were used. Do not
promote these short-timeout test configs into production install defaults.
