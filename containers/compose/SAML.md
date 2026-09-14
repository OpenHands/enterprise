# Test SSO locally with MockSAML

Use the optional MockSAML overlay to test SAML sign-in alongside local password
login. Complete the [Compose setup](README.md#start) first. Keep your existing
`.env`, administrator credentials, and data volumes.

## Start

From the repository root, generate the test IdP's signing credentials:

```sh
python3 containers/compose/setup_saml.py
docker compose -f docker-compose.yml -f docker-compose.saml.yml up --build -d
docker compose -f docker-compose.yml -f docker-compose.saml.yml ps
```

The helper requires OpenSSL and creates the private, untracked `.mocksaml/`
directory. Running it again keeps the same certificate and key. The base stack
continues to use password login when started without the overlay.

Open the application using the `OPENHANDS_PORT` selected in `.env` (for example,
`http://localhost:3000`). On the sign-in page, choose **Sign in with SSO**.
Keep the default username `jackson` and selected domain `@example.com`, then choose
**Sign In**. The resulting identity is `jackson@example.com`. Enter only the
username in MockSAML's **Email** field; the selected domain is appended for you.
MockSAML accepts any password and creates the test user's OpenHands account on
first sign-in. You can choose other usernames under `example.com` or `example.org`.

Start each login from OpenHands so it can verify the browser session. MockSAML
runs at `http://localhost:4000` by default. Both services bind to localhost and
are intended for a browser running on the Docker host.

## Existing accounts and password fallback

The original administrator can still sign in with email and password. Keep the
default MockSAML identity separate from that administrator while testing.

If a SAML email matches an existing local account, OpenHands requires explicit
linking. Sign in with that account's password, open **Settings > User**, and choose
**Link SSO to this account** under **Sign-in methods**. Matching email addresses
alone do not link accounts.

## Ports, certificates, and restarts

To change the IdP port, add `MOCKSAML_PORT` to `.env` with an available port, then
rerun the same Compose `up` command. The application and MockSAML receive matching
public URLs. This does not change the application port or signing certificate.

Keep `.mocksaml/` across restarts. It holds `idp.crt`, `idp.key`, and
`mocksaml.env`. Only the public certificate is mounted into the application and
initialization services. Treat the private key and environment file as local
credentials, and do not commit them.

To replace an expired or lost certificate, stop the services with the same two
Compose files, move `.mocksaml/` to a private backup location, run the setup helper,
and rerun the Compose `up` command. This updates both sides of the trust
configuration. Keep `NATIVE_SAML_CONNECTION_ID` and the IdP entity ID unchanged
to preserve linked identities. Start a fresh login after changing certificates.

Use both Compose files for later operations on this stack:

```sh
docker compose -f docker-compose.yml -f docker-compose.saml.yml stop
docker compose -f docker-compose.yml -f docker-compose.saml.yml start
```

The [main guide](README.md#stop-restart-and-remove) covers sandbox cleanup and data
volumes. For external identity providers, use the
[native SAML configuration guide](../../docs/native-saml.md).
