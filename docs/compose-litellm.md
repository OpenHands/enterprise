# Optional LiteLLM gateway for Docker Compose

Add LiteLLM to manage per-user and organization budgets with managed gateway
keys while retaining usage visibility in OpenHands. The default Compose stack
continues to use direct provider credentials; this overlay is optional. It adds
LiteLLM and a separate PostgreSQL database and enables the existing managed LiteLLM integration on both
the app and worker. It does not change application code or configure SSO.

This example uses Anthropic Haiku. It is intended for the same single-machine
evaluation use case as the base Compose stack. The current base defaults to
Enterprise **1.88.0**, with the same image for app and worker. Earlier native
usage/budget and Okta acceptance used Enterprise **1.87.0** / LiteLLM **1.94.1**
with an equivalent experimental configuration, not these exact overlay files.
Full-app validation on 1.88.0 is recorded separately below.
Earlier Compose releases without the usage-callback fix in
[Enterprise #1207](https://github.com/OpenHands/enterprise/pull/1207) can run
inference while showing zero persisted dashboard usage.

## Configure and start

1. Get an Anthropic API key with access to the configured model. In the repository
   root, create or update the Git-ignored `.env` file without replacing existing
   settings. Generate separate values with `openssl rand -hex 32` for the master
   key and salt, and `openssl rand -hex 24` for the database password:

   ```dotenv
   ANTHROPIC_API_KEY=<your-provider-key>
   LITELLM_MASTER_KEY=sk-<first-random-value>
   LITELLM_SALT_KEY=<second-random-value>
   LITELLM_DB_PASSWORD=<third-random-value>
   ```

   Replace every placeholder. Use a hexadecimal database password so it is safe
   in the database URL. Restrict the file with `chmod 600 .env`. Preserve the salt
   and database credentials with your backups; do not regenerate them on restart.
   Never enter the master key in a user's model settings or personal secrets.

2. Check the configuration, then start with both files:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.litellm.yml config --quiet
   docker compose -f docker-compose.yml -f docker-compose.litellm.yml up --wait
   ```

   Use `config --quiet`: the expanded configuration contains secrets. LiteLLM and
   its database have no published host ports and are not routed by Caddy. The
   gateway can reach the provider; its database uses an internal network. A
   healthy container is a startup check, not proof of a working provider key or
   correct user/team accounting.

   For the existing HTTPS setup, include its file in every command, for example:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.https.yml -f docker-compose.litellm.yml up --wait
   ```

3. Complete OpenHands onboarding using the existing Compose instructions. Use
   the gateway-managed model, shown as `openhands/claude-haiku-4-5-20251001`,
   rather than entering a shared provider key. The corresponding gateway route
   is `claude-haiku-4-5-20251001`; `litellm_proxy/` is the deployment-default prefix.
   **Fresh-account onboarding is not qualified by the existing-user acceptance.**
   The application has a gateway provisioning path, but first-admin signup and
   second-user onboarding must be tested separately before relying on automatic
   team/user/key creation. Verify the bindings below before enabling budgets.
   Existing installations require the migration steps in the next section.

To use another provider, change `litellm/config.yaml`, pass only the provider
environment variables that configuration needs to the `litellm` service, and
update `LITELLM_DEFAULT_MODEL` in the overlay to match its route. Recreate the
services with the same file set and validate that provider/model separately.

## Migrate an existing installation

Enabling the overlay does **not** automatically migrate existing direct-provider
users or remove their old shared credentials. Schedule the switch, back up the
application and gateway databases and preserve the previous settings privately.
Use the existing organization and native user IDs, not newly created application
accounts. Keep an administrator's recovery login available.

1. Ensure the LiteLLM team has `team_id` equal to the existing OpenHands
   organization ID. Ensure each LiteLLM user has `user_id` equal to the existing
   native OpenHands user ID and belongs to that team. These are gateway
   accounting records, not new OpenHands organizations or users. Obtain the
   current user/org IDs from `GET /api/v1/users/me`; an administrator can list
   the existing users with `GET /api/organizations/{org_id}/members`. Use the
   installation's `/docs` to inspect these supported APIs.

   An administrator can use LiteLLM's management APIs from inside the gateway
   container. Use its private `LITELLM_MASTER_KEY` only for those calls. For an
   isolated new gateway, the relevant requests are:

   | Request | Body / lookup |
   | --- | --- |
   | `GET /team/info` | Query `team_id=<existing-org-id>` |
   | `POST /team/new` if absent | `{"team_id":"<existing-org-id>","team_alias":"OpenHands"}` |
   | `GET /user/info` | Query `user_id=<existing-native-user-id>` |
   | `POST /user/new` if absent | `{"user_id":"<existing-native-user-id>","teams":["<existing-org-id>"],"auto_create_key":false,"send_invite_email":false}` |
   | `POST /team/member_add` if membership is absent | `{"team_id":"<existing-org-id>","member":{"user_id":"<existing-native-user-id>","role":"user"}}` |

   For example, verify management authentication without printing a credential
   or response payload:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.litellm.yml exec -T litellm python - <<'PYTHON'
   import os
   import urllib.request

   request = urllib.request.Request(
       "http://localhost:4000/model/info",
       headers={"Authorization": "Bearer " + os.environ["LITELLM_MASTER_KEY"]},
   )
   with urllib.request.urlopen(request, timeout=10) as response:
       print("Gateway management HTTP", response.status)
   PYTHON
   ```

   Read before creating; preserve existing budgets and membership. Do not copy
   gateway administration credentials into a browser model profile or logs.
   See [LiteLLM key management](https://docs.litellm.ai/docs/proxy/virtual_keys).

2. As an organization administrator, configure and activate the organization
   model profile for `openhands/claude-haiku-4-5-20251001` with base URL
   `http://litellm:4000`, without a static profile API key. Clear the former
   organization-wide provider key through the supported organization settings
   API (`PATCH /api/organizations/{org_id}/settings`, `{"llm_api_key":""}`).
   Consult the installation's `/docs` schema and verify the intended organization
   before writing. Saving a profile alone was insufficient in the evaluated
   migration while the old organization key was still present.
3. Have each user select the managed model and save their settings. The supported
   `POST /api/v1/settings` path provisions or validates their managed key. Do not
   supply a shared external key. Start fresh conversations after the switch;
   existing conversations may retain launch-time credentials.
4. Verify that different users have different gateway keys and that each key's
   `user_id` and `team_id` match the native user and organization. Inspect this
   privately through the gateway administration APIs; never publish key values.
   Do not enable budgets until these checks pass.

The earlier equivalent-configuration migration was exercised with two existing
users. Fresh-account onboarding was not part of that acceptance run.

## Usage and budget acceptance

As an organization administrator, open **Usage** and **Budgets** in Settings.
Configure the organization budget and any personal limits through OpenHands,
then require healthy budget reconciliation. `ENABLE_BILLING=false` remains the
Compose default: this is budget policy, not a requirement to enable billing.
The gateway must retain its database for managed keys and spending records.

Before treating the setup as accepted:

1. In separate sessions, run a small task as each of two users. Confirm persisted
   token and cost records in Usage, the correct native conversation owner, and
   gateway charges against the expected user/team. Live agent counters alone do
   not establish dashboard persistence.
2. Temporarily cap one user's budget below their cycle spend. Confirm a real
   follow-up is rejected, while the other user can still complete a request.
   Restore the limit and repeat in the other direction.
3. Test the organization cap separately and verify the expected shared denial.
   Restore the original policy without deleting usage or resetting counters.

Use enough budget headroom for request reservations and a normal output limit
(the acceptance used 4,096 tokens and low reasoning). A tiny cap can reject the
first request even though completed spend is zero. Gateway totals can include
helper calls such as title generation that are outside the main conversation
ledger. Native budget cycles can also have a different baseline from gateway
lifetime spending; compare corresponding periods and allow spend writes to settle.

## Full-app validation on Enterprise 1.88.0

On November 6, 2026, the optional gateway was exercised with the base Compose
file and the automatic HTTPS overlay, using two existing users in one shared
organization. App and worker used the base file's Enterprise **1.88.0** image;
LiteLLM was **1.94.1**.

Okta sign-in and the direct-provider switch/return ran on identical 1.88.0 app
configuration before removing two gateway settings that LiteLLM 1.94.1 does not
read. Native terminal tasks, reciprocal personal limits and both organization-cap
denials were repeated on the final files; the table labels the earlier checks.

| Check | Observed result |
| --- | --- |
| Existing-user setup before cleanup; final bindings rechecked | Two distinct managed keys, each bound to the correct native user and organization |
| Native terminal write/read tasks, one per user | Both passed; positive persisted tokens/cost and correct conversation ownership |
| Native Usage dashboard | Positive usage/spend visible; both users present |
| Reciprocal personal budget controls | Each capped user was denied while the other could continue; denied requests added no native cost/tokens |
| Okta login, before final gateway cleanup | Same existing Member, organization and role; fresh terminal task and persisted Member usage passed |
| Organization cap after Okta login | Owner and Member follow-ups both denied with unchanged native ledgers |
| Direct-provider switch, before final gateway cleanup | Fresh terminal task and persisted usage passed; gateway spend stayed unchanged |
| Managed return, before final gateway cleanup | Fresh request passed; distinct correctly bound managed keys and healthy native budget reconciliation restored |

The run retained both application and gateway data. Re-enabling the native policy
explicitly recorded a new cycle baseline against retained gateway lifetime
spending; it did not erase spending. Temporary personal limits were removed, and the direct-control provider key was
cleared from organization settings and its temporary profile afterward.
Ten application/gateway services remained running with seven healthy configured
health checks and no OOM flags.

HTTP and supplied-certificate HTTPS combinations were configuration-checked;
their full-app flows were not separately rerun. Fresh-account signup, production
hardening, other model providers, and other identity providers remain unqualified.

The tested UI could retain an earlier budget-error banner after a successful
recovery. Verify the latest agent response and persisted ledger rather than
interpreting an old banner alone as a new rejection.

## SSO compatibility: tested with Okta

Existing-account sign-in through **Okta OIDC** was repeated on Enterprise
**1.88.0** / LiteLLM **1.94.1**, before the gateway-only cleanup described in the
validation section. After application logout and Okta
sign-in, the account retained its existing OpenHands user ID, shared organization
and Member role. A fresh terminal task and persisted per-user usage passed, and
the subsequent organization-cap check rejected both users. Reciprocal personal
budget controls also passed in that same build before the fresh login.

The earlier Enterprise 1.87.0 experiment used an equivalent configuration and
validated individual budget controls after Okta sign-in. These are existing-account
compatibility checks, not fresh-user provisioning acceptance.

SSO authenticates the person to OpenHands; OpenHands uses the mapped native user
and organization to select the managed gateway credential. LiteLLM does not need
its own Okta login for these inference requests. Other identity providers may
work through the same mapping, but were **not tested** in this acceptance.

The Okta test deliberately linked an existing native account and reused an
existing Okta session. It did not qualify fresh-user provisioning, SCIM,
group-to-organization mapping, offboarding, a fresh MFA challenge, or production
MFA policies. The test used a separately configured **Super Admin → Auth Providers
→ Okta OIDC** connection, with its callback registered in an Okta web application
and an explicit link to the existing native account. That configuration is
outside these files and is not installed by the base Compose stack or this
overlay. This note records compatibility; it is not an Okta setup guide.

## Restart, backup and disable

Use the same `-f` file set for every lifecycle command. To make the opt-in persist
for ordinary `docker compose` commands, set this in the Git-ignored `.env` file
(use colon separators on Linux/macOS; semicolons on Windows):

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.litellm.yml
```

Include your HTTPS file in that list when applicable. An explicit `-f` list
overrides `COMPOSE_FILE`, so use the complete intended list. `down` preserves named
volumes; `down -v` deletes data, including gateway keys and spending history.
Back up both PostgreSQL databases and the private configuration together. See
[LiteLLM's deployment guidance](https://docs.litellm.ai/docs/proxy/deploy) for
operational requirements beyond this evaluation example.

To return to direct-provider mode, first preserve settings and prepare a working
direct-provider profile. While the gateway is still enabled, disable its native
budget policy through **Budgets** and verify reconciliation; preserve usage and
policy settings for a later return. If you persisted `COMPOSE_FILE`, remove
`docker-compose.litellm.yml` from that list in `.env` and from any shell export,
keeping the base and your HTTPS file. Otherwise a later ordinary `docker compose
up` would silently enable the gateway again. Then recreate app and worker using
only the original file set (`docker compose -f docker-compose.yml up -d --no-deps app worker`, plus your
usual HTTPS file if applicable). Configure direct-provider credentials through
OpenHands and verify a fresh request. Removing the overlay does not translate
stored managed profiles into direct-provider profiles.

Stop the unused gateway services without deleting their volumes:

```bash
docker compose -f docker-compose.yml -f docker-compose.litellm.yml stop litellm litellm-db
```

When returning to the managed overlay, add `docker-compose.litellm.yml` back to
your persisted `COMPOSE_FILE` list (or use the complete explicit `-f` list), start
that configuration, and follow the migration/key-binding checks again before
re-enabling its native budget policy. Re-enabling establishes a new
native cycle baseline against existing gateway spend; it does not clear gateway
spending history.

Retain their volumes until accounting history and rollback are no longer needed.
Do not use `--remove-orphans` or `down -v` as part of an ordinary provider switch.
