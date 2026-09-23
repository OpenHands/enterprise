# Caddy supplied-certificate overlay

Follow the common README first. Set `TLS_CERT_DIR` to an absolute directory
containing `cert.pem` (leaf plus intermediates) and `key.pem`. The POC publishes
loopback HTTPS on 8443. Use a certificate covering your test hostname/IP and trust
its CA explicitly; never disable TLS verification in the checks.

From `containers/compose`:

```sh
docker compose --env-file .env -f compose.yaml -f caddy.yaml config --quiet
docker compose --env-file .env -f compose.yaml -f caddy.yaml up -d
docker compose --env-file .env -f compose.yaml -f caddy.yaml exec proxy caddy validate --config /etc/caddy/Caddyfile
docker compose --env-file .env -f compose.yaml -f caddy.yaml exec proxy caddy reload --config /etc/caddy/Caddyfile
docker compose --env-file .env -f compose.yaml -f caddy.yaml down
```

Replace certificates atomically in their mounted directory, validate and reload.
This profile uses supplied certificates; it does not exercise public ACME.
Enterprise streams have no response-header timeout here. Automation has a 15s
response-header wait and a 16-request active limit; excess work receives 503.
Uploads/body transfer and established streaming connections have different
semantics from the first-header wait. Do not call this a total request deadline.
Reload keeps established streams for up to one minute. These are POC settings
whose workload fit still needs authenticated execution and soak testing.

To swap candidates, retain the same Compose project and base, substitute the
overlay and run `up -d --no-deps proxy`. The proxy's reserved IP keeps app trust
settings unchanged. A single proxy replacement has a service interruption.
