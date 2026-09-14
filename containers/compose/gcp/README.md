# Run Enterprise with HTTPS on a Google Cloud VM

Use the GCP and authentik overlays with the base Compose stack. The application
uses native authentication and direct LLM credentials. Caddy provides HTTPS for
the application, identity provider, and sandbox services.

## Configure the deployment

Use a Linux VM with a current Docker Engine and Compose. Allow public TCP ports
80 and 443. Restrict SSH to your administration access. Keep the VM's service
account unset, because sandbox processes can reach the metadata service.

Create a dedicated public Cloud DNS zone for the deployment and delegate it from
its parent. Point the application hostname, identity provider hostname, and
wildcard sandbox DNS record at the VM's static public address.

Create a certificate service account with `dns.managedZones.list` on the project
and record management permissions on the dedicated zone. The pinned
[Google Cloud DNS provider](https://github.com/caddy-dns/googleclouddns) discovers
zones by listing the project; it has no explicit zone setting. Limit write access
using [zone IAM](https://docs.cloud.google.com/dns/docs/zones/iam-per-resource-zones).
Store its JSON credential at `.gcp/caddy-dns.json` with mode `0600`.

Prepare a private environment file on your workstation using the
[base Compose setup](../README.md):

```sh
install -d -m 700 .gcp
python3 containers/compose/setup.py --output .gcp/remote.env
```

Keep its generated database, encryption, and bootstrap credentials. Set
`DOCKER_SOCKET_PATH=/var/run/docker.sock` for the Linux VM, because setup detects
the workstation's socket. Add these deployment settings to the same file:

```dotenv
COMPOSE_PROJECT_NAME=openhands-remote
OPENHANDS_HOST=app.example.com
AUTHENTIK_HOST=auth.example.com
SANDBOX_DOMAIN=sandboxes.example.com
GCP_PROJECT=your-dns-project
ACME_EMAIL=operator@example.com
CADDY_DNS_CREDENTIALS_FILE=./.gcp/caddy-dns.json
COMPOSE_INGRESS_SUBNET=172.30.40.0/24
```

Choose an ingress subnet that does not overlap existing VM or Docker networks.
The application and authentik trust forwarded headers only from this subnet.
Caddy uses an ingress-only application alias so sandbox callbacks do not inherit
that trust.

Complete the [authentik setup](AUTHENTIK.md), including its SAML trust file, before
enabling SAML. Copy `.gcp/remote.env` to the deployment repository's root `.env`
on the VM with mode `0600`, and copy the DNS credential to `.gcp/caddy-dns.json`.
The VM's root `.env` is the authoritative configuration. Start the combined stack
from that repository root on the VM:

```sh
docker compose -f docker-compose.yml -f docker-compose.gcp.yml \
  -f docker-compose.authentik.yml up --build -d
```

Use the same Compose file arguments for later commands on the VM. Authentik
provisioning updates the VM's `.env` to enable SAML; copy that updated file back
to your private workstation backup afterward. Do not pass the earlier
`.gcp/remote.env` as an override when operating the VM.

## Connect GitHub through OAuth

Register the GitHub application's callback URL as
`https://<OPENHANDS_HOST>/oauth/git/github/callback`, using your application
hostname. Add its client credentials to the VM's authoritative root `.env`:

```dotenv
NATIVE_GIT_GITHUB_OAUTH_ENABLED=true
GITHUB_APP_CLIENT_ID='your-client-id'
GITHUB_APP_CLIENT_SECRET='your-client-secret'
```

Keep `.env` at mode `0600` and synchronize your private workstation backup after
editing it. OAuth is disabled by default. The GCP overlay passes these credentials
only to the application service.

Recreate the application container to load the updated environment:

```sh
docker compose -f docker-compose.yml -f docker-compose.gcp.yml \
  -f docker-compose.authentik.yml up -d --no-deps --force-recreate openhands
```

Wait for `/ready` to return HTTP 200, then connect GitHub from **Settings >
Integrations**. Each user completes GitHub authorization for their own account.

## Sandbox routing

A sandbox service URL has the form
`https://8000-<resource-id>.sandboxes.example.com`. Caddy resolves the corresponding
managed container through Docker DNS and permits only the configured Agent Server,
VS Code, and two preview ports. Sandbox host ports remain bound to loopback.
Browser and backend requests use HTTPS; WebSocket connections use WSS.

Sandbox callbacks and credential lookups continue to use `http://openhands:3000`
on the sandbox network. Caddy has no Docker socket or database connection. Its DNS
credential is mounted only into the proxy, and its administration listener remains
on container loopback. The application's CSP permits the configured sandbox HTTPS
suffix for connections and embedded previews.

Existing sandboxes retain the routing saved at creation. Use a separate deployment
and data volumes when trying this remote configuration alongside the local stack.

## Keep the deployment available

Preserve the application and authentik databases, private environment files,
sandbox workspace volumes, and Caddy's data volume. Caddy renews certificates
through DNS-01 while the delegated zone and certificate credentials remain valid.
Back up credentials with mode `0600`; `.gcp/` is ignored by Git.

Verify a new conversation, its VS Code tab, preview, and pause/resume after a
deployment change. Verify public high ports remain inaccessible and the application
container can reach the public sandbox HTTPS URLs. Configure LLM credentials in
the application Settings UI.
