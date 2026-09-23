# nginx supplied-certificate overlay

Follow the common README and set `TLS_CERT_DIR` to an absolute directory with
`cert.pem` (leaf plus intermediates) and `key.pem`. Supply a certificate covering
the public hostname/IP; trust its CA explicitly in tests. Public access is
loopback HTTPS on 8443 unless the deployment changes the port mapping.

From `containers/compose`:

```sh
docker compose --env-file .env -f compose.yaml -f nginx.yaml config --quiet
docker compose --env-file .env -f compose.yaml -f nginx.yaml up -d
docker compose --env-file .env -f compose.yaml -f nginx.yaml exec proxy nginx -t
docker compose --env-file .env -f compose.yaml -f nginx.yaml exec proxy nginx -s reload
docker compose --env-file .env -f compose.yaml -f nginx.yaml down
```

Replace certificates atomically in the directory, validate, then reload.
This tests supplied certificates, not ACME. Docker DNS resolves recreated apps.
Automation allows 16 active upstream connections in a shared zone, with no
request retry and a 15s upstream read inactivity timeout. Excess work can receive
502; there is no explicit queue here. Enterprise read inactivity timeout is one
hour for streaming. Neither limit is a total request deadline. Uploads are capped
at 100 MiB; request buffering remains enabled and must be evaluated for real
upload workloads. Response buffering is disabled to preserve SSE delivery.

Swap with another candidate by retaining the same project/base and running its
overlay with `up -d --no-deps proxy`. Apps keep running; the reserved proxy address
preserves trusted-header settings. Expect an interruption while replacing the
single proxy. These are evaluation settings, pending authenticated execution,
maintenance and soak checks.
