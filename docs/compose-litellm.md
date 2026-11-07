# Optional LiteLLM gateway for Docker Compose

Use this overlay to enable OpenHands-managed per-user and organization budgets.
It adds LiteLLM on the default Compose network and stores gateway keys and
spending in a separate database within the existing PostgreSQL service.
Without the overlay, OpenHands continues to use direct provider credentials.

## Start the gateway

Choose a model provider and supply its credentials. This example uses Anthropic
and Claude Haiku; Anthropic is not required for the integration. To use another
provider, change the model configuration and provider environment variables as
described below.

For the Anthropic example, add your API key to the repository's Git-ignored
`.env` file, preserving any existing settings:

```dotenv
ANTHROPIC_API_KEY=<your-provider-key>
```

Start the stack with both files:

```bash
docker compose -f docker-compose.yml -f docker-compose.litellm.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.litellm.yml up --wait
```

For HTTPS, include your existing HTTPS overlay in every command:

```bash
docker compose -f docker-compose.yml -f docker-compose.https.yml -f docker-compose.litellm.yml up --wait
```

LiteLLM creates its database on startup. It uses the base stack's `DB_USER` and
`DB_PASS`, whose role must have permission to create databases. The overlay
publishes no gateway port. Use `config --quiet` to avoid printing secrets.

These defaults follow the base stack's single-machine evaluation setup:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LITELLM_MASTER_KEY` | `sk-local-compose-master-key` | Gateway management authentication |
| `LITELLM_SALT_KEY` | `local-compose-salt-key` | Gateway credential encryption |
| `LITELLM_DB_NAME` | `litellm` | Gateway database, separate from the application database |

For a shared deployment, set the key and salt before the gateway's first start,
and `DB_PASS` before PostgreSQL is first initialized. Keep the existing `DB_PASS`
when adding the gateway to an existing installation.

Generate independent key and salt values with `openssl rand -hex 32`, prefix the
master key with `sk-`, and store them in `.env`. Keep the salt unchanged and
preserve it with your backups. Never enter the master key in a user's model
settings. Database credentials used in the connection URL must be URL-safe.
`LITELLM_DB_NAME` must differ from `DB_NAME` and `AUTOMATION_DB_NAME`.

To change models or providers, edit `dev_config/litellm/config.yaml`, pass the
required provider environment variables to `litellm`, and update
`LITELLM_DEFAULT_MODEL` in the overlay to match its route.

## Connect OpenHands users

Complete the base stack's onboarding. For the Anthropic/Haiku example above, the
managed model is `openhands/claude-haiku-4-5-20251001`, with gateway base URL
`http://litellm:4000`. If you configured another model or provider, select its
corresponding managed model instead; the gateway base URL stays the same.
Each user must use their own managed key for individual accounting and budgets.
For every installation, verify through the gateway management APIs that users
have distinct keys with the expected `user_id` and `team_id` before enabling
budgets.

For an existing direct-provider installation, back up settings and data before
switching. Enabling the overlay does not migrate existing credentials:

1. Ensure the gateway has a team whose `team_id` matches the OpenHands
   organization ID, and a user whose `user_id` matches each native user ID.
   Add those users to the team. Obtain native IDs through
   `GET /api/v1/users/me` and `GET /api/organizations/{org_id}/members`.
2. Use [LiteLLM's management APIs](https://docs.litellm.ai/docs/proxy/virtual_keys)
   inside the gateway container with its master key. Read existing records before
   creating them; preserve existing membership and budgets. Create a team or user
   only if absent; add team membership only if missing.

   | Request | Body |
   | --- | --- |
   | `POST /team/new` | `{"team_id":"<org-id>","team_alias":"OpenHands"}` |
   | `POST /user/new` | `{"user_id":"<user-id>","teams":["<org-id>"],"auto_create_key":false,"send_invite_email":false}` |
   | `POST /team/member_add` | `{"team_id":"<org-id>","member":{"user_id":"<user-id>","role":"user"}}` |

3. Activate an organization model profile for the managed model and base URL,
   without a static profile API key. Clear the former organization provider key
   through `PATCH /api/organizations/{org_id}/settings` with
   `{"llm_api_key":""}`. Consult your installation's `/docs` for API schemas.
4. Have each user select the managed model and save Settings. This provisions or
   validates their managed key. Start new conversations after switching;
   existing conversations can retain their original credentials.

## Usage, budgets and SSO

Use **Settings → Usage** to view token and cost records, and **Settings → Budgets**
to configure organization and personal limits. Billing can remain disabled.
Verify that a task records usage for its owner and that a capped user's request
is denied while another user can continue. Allow headroom for request
reservations; gateway totals can include helper calls outside conversation usage.

SSO authenticates users to OpenHands. The mapped native user and organization
select the managed gateway credential; LiteLLM needs no separate SSO login for
inference. Configure SSO separately in **Super Admin → Auth Providers**; this
overlay does not configure an identity provider or its provisioning rules.

## Restart, backup and disable

Use the same overlay files for every lifecycle command. To persist that selection
on Linux/macOS, add this to `.env` (use semicolons on Windows):

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.litellm.yml
```

Include your HTTPS overlay when applicable. Back up both the application and
LiteLLM databases in the shared PostgreSQL service, along with private settings.
`down` preserves named volumes; `down -v` deletes the shared database volume and
both databases.

To return to direct-provider mode, disable the budget policy in OpenHands,
remove the LiteLLM overlay from `COMPOSE_FILE`, and recreate app and worker with
your original file set. Configure direct-provider credentials in OpenHands and
start a new conversation. Stop the unused gateway with the full overlay file set:

```bash
docker compose -f docker-compose.yml -f docker-compose.litellm.yml stop litellm
```

Retain the database and salt for a later return. Re-enable the overlay, repeat the
key-binding checks, and then enable budgets. Re-enabling a budget policy starts a
new native cycle baseline without clearing gateway spending history.
