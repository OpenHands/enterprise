# Add authentik SAML to the hosted Compose deployment

Use this overlay with the [GCP deployment](README.md). The application keeps native email/password login alongside SSO. Start sign-in from the OpenHands login page.

## Prepare credentials

Set `OPENHANDS_HOST` and `AUTHENTIK_HOST` in the private remote environment file, then run:

```bash
python3 containers/compose/gcp/authentik_setup.py prepare
```

The helper creates authentik administrator credentials and two ordinary demo users in `.gcp/credentials.json`. It preserves the existing OpenHands credentials and reuses generated passwords on subsequent runs. The `.gcp/` directory is private and ignored by Git.

Copy `.gcp/remote.env` to the VM as `.env`. Copy the other `.gcp/` files to the same relative directory on the VM, preserving private permissions. The bootstrap environment file belongs only to the authentik worker.

## Start and configure the identity provider

From the deployment directory on the VM, start the identity provider:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp.yml -f docker-compose.authentik.yml up -d authentik-postgresql authentik-server authentik-worker
python3 containers/compose/gcp/authentik_setup.py check-bootstrap --env-file .env
```

The first bootstrap can take several minutes while database migrations run. Repeat the check until it reports `bootstrap_ready`, then start the proxy. This keeps the initial setup flow private until the administrator account is claimed.

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp.yml -f docker-compose.authentik.yml up -d caddy
```

Wait for the configured identity hostname to serve HTTPS, then provision the users and SAML connection:

```bash
python3 containers/compose/gcp/authentik_setup.py provision --env-file .env
```

The helper creates a dedicated signing certificate, exports its public certificate to `.gcp/authentik-idp-certificate.pem`, validates the identity provider metadata, and enables SAML in `.env`. It reports metadata URLs, certificate fingerprint, and non-secret account identifiers. It leaves the private signing key in authentik's database.

The pinned authentik release retains a completed `ForceAuthn` marker in the browser session. The helper creates an authorization flow used only by OpenHands that clears this marker after authentication succeeds. This ensures each subsequent forced reauthentication asks for credentials and provides a fresh signed login timestamp. The policy disables caching for its own evaluation; application group restrictions and OpenHands assertion freshness checks remain enforced. Recheck this workaround when upgrading authentik.

Start the application after provisioning completes:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp.yml -f docker-compose.authentik.yml up -d
```

Choose **Sign in with SSO** on the OpenHands login page. The two demo accounts have no administrator privileges. Their authentik email addresses and usernames cannot be changed through the default profile settings flow. Keep each account's email unchanged after its first OpenHands sign-in.

Use separate private browser windows when testing Alice and Bob, or sign out of authentik before switching identities. Signing out of OpenHands leaves the authentik session available for the next SSO login; this deployment does not configure SAML single logout. The authentik administrator and demo passwords are in `.gcp/credentials.json`. OpenHands bootstrap credentials remain in the private deployment `.env`, with its workstation backup in `.gcp/remote.env`.

## Finish bootstrap

After validating sign-in, revoke the temporary bootstrap API token and remove its worker environment:

```bash
python3 containers/compose/gcp/authentik_setup.py finalize --env-file .env
docker compose -f docker-compose.yml -f docker-compose.gcp.yml -f docker-compose.authentik.yml up -d --force-recreate authentik-worker
```

Copy the updated `.env`, public trust file, sanitized summary, and empty bootstrap environment file back to the private local deployment directory. The authentik administrator password remains in `.gcp/credentials.json`.

For later configuration updates, create a temporary administrator API token and supply its private file with `--token-file`. Revoke that token after use. Repeating preparation does not recreate a revoked bootstrap token.

## Preserve identity and trust

Back up the authentik PostgreSQL volume, its `/data` volume, private environment configuration, and the proxy's TLS storage. Preserve the existing volumes and deployment project name when restarting or updating the stack. The identity database contains user identities, application configuration, and the signing private key.

Restart OpenHands after replacing the mounted identity provider certificate. For certificate rollover and native authentication behavior, see [native SAML configuration](../../../docs/native-saml.md).

The overlay pins a stable authentik release. Review [authentik release notes](https://docs.goauthentik.io/releases/) before changing the image pin, and follow the [backup guidance](https://docs.goauthentik.io/sys-mgmt/ops/backup-restore/).
