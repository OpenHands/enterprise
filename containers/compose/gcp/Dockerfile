FROM caddy:2.11.4-builder AS builder
RUN xcaddy build v2.11.4 \
    --with github.com/caddy-dns/googleclouddns@6fcdbd98a8565df50ff40ff53411dc6c8d97dcd9

FROM caddy:2.11.4
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
