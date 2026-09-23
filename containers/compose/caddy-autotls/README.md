# Caddy auto-TLS overlay (single origin, local or hosted)

One reverse-proxy overlay that serves Enterprise and automation under a single
origin and picks its TLS method from `SITE_ADDRESS`:

- **Local** — `SITE_ADDRESS=localhost` (default): Caddy issues a certificate from
  its own internal CA. Works offline, no DNS, no public ports.
- **Hosted** — `SITE_ADDRESS=proxy.example.com`: Caddy automatically obtains and
  renews a Let's Encrypt certificate via ACME.

Routing is identical in both modes: `/api/automation` and its descendants go to
`automation:8000` (prefix preserved), everything else to `enterprise:3000`.

This overlay is separate from `caddy.yaml`, which pins a supplied certificate on
loopback `:8443`. Use one overlay or the other, not both.

## Local

From `containers/compose` (defaults to `localhost`, high ports avoid privileges):

```sh
HTTP_PORT=8080 HTTPS_PORT=8443 \
  docker compose --env-file .env -f compose.yaml -f caddy-autotls.yaml up -d
```

Browse `https://localhost:8443`. To make the browser trust Caddy's local CA:

```sh
docker compose --env-file .env -f compose.yaml -f caddy-autotls.yaml exec proxy \
  caddy trust
```

Or export the root for a client/CA bundle:

```sh
docker compose --env-file .env -f compose.yaml -f caddy-autotls.yaml exec proxy \
  cat /data/caddy/pki/authorities/local/root.crt > caddy-local-root.crt
```

## Hosted (when you choose)

Requirements: a public DNS `A`/`AAAA` record for the hostname pointing at this
machine, and inbound TCP `80` and `443` reachable from the internet. Port 80 is
used for the ACME challenge and the HTTP->HTTPS redirect; keep `caddy-data`
persisted so certificates and the ACME account survive restarts.

```sh
SITE_ADDRESS=proxy.example.com HTTP_PORT=80 HTTPS_PORT=443 \
  docker compose --env-file .env -f compose.yaml -f caddy-autotls.yaml up -d
```

Set `PUBLIC_HOST` (in `.env`) to the same hostname so the applications build
correct absolute URLs. For a Let's Encrypt contact address, add a global options
block `{ email you@example.com }` at the top of the Caddyfile.

## Company-provided certificate

To use a supplied certificate instead of ACME, add `tls /certs/cert.pem
/certs/key.pem` inside the site block, mount the certificate directory into the
proxy, and set `SITE_ADDRESS` to the matching hostname.

## Notes

- Persist `caddy-data` (issued certs + ACME account) to avoid re-issuing and
  Let's Encrypt rate limits.
- The proxy keeps the reserved network address so the applications' trusted
  forwarding-header settings stay stable across proxy replacement.
- Automatic HTTPS is on; do not also mount the supplied-cert Caddyfile here.
