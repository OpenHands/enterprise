# Implementation handoff: make Keycloak optional

The user has finished planning and will request implementation in the next prompt. If the next message is "get started", implement the [technical plan](keycloak-removal-technical-plan.md), beginning with its first stage and continuing toward the complete initial release scope. The [leadership plan](keycloak-removal-leadership-plan.md) is the communication summary. Read the technical plan before making implementation decisions. This handoff preserves context; it does not supersede that plan.

The user wants OpenHands Enterprise to operate without Keycloak. New installations default to email/password authentication and bootstrap an administrator. Existing Keycloak installations retain their behavior. Customers requiring SAML/OIDC continue using Keycloak until native support is delivered.

The user's strongest design constraint is to be opinionated: choose one implementation and one configuration path for each new behavior. Do not add alternative backends, settings, aliases, or input methods for hypothetical flexibility. In particular, bootstrap accepts exactly `OH_BOOTSTRAP_ADMIN_EMAIL` and `OH_BOOTSTRAP_ADMIN_PASSWORD` from environment variables. There is no file-based bootstrap input. Keycloak compatibility is an explicit migration requirement, not a reason to make new local authentication configurable in multiple ways.

The agreed implementation decisions are:

- Use FastAPI Users with `pwdlib` and Argon2id for local password authentication. Its maintenance mode is acceptable; security and dependency maintenance continue. The library comparison is complete and recorded in the technical plan.
- OpenHands owns account identity, account lifecycle, authorization, public routes, and request/response schemas. The local package uses a custom FastAPI Users database adapter over the existing `User` plus local credentials. Library models and exceptions stay inside the integration. Thin OpenHands routes call the shared services; generic library CRUD must not bypass account lifecycle rules.
- Keep the existing `get_user_auth`, `SaasUserAuth`, and `UserContext` entry points as compatibility adapters while extracting the six contracts described in the technical plan. Separate authentication, user management, external identity mapping, password credentials, browser sessions, and integration credentials.
- Add the startup flag `KEYCLOAK_ENABLED`. Accept `true`/`1` and `false`/`0`. Fresh installations select local authentication. Existing installations retain Keycloak through recorded installation state and explicit legacy detection. No fallback to local passwords during a Keycloak outage, no per-user flag, and no automatic identity migration when a populated installation changes the flag.
- Preserve existing user IDs, organization relationships, permissions, API keys, and Keycloak-mode callback/cookie behavior. Generate UUIDs for new local users and continue the personal-organization ID relationship. Keep existing physical columns named `keycloak_user_id` during this release.
- Put unique normalized login emails and password hashes in local credential records. Legacy `User.email` can contain duplicates. Preserve dots and plus addressing. External identity linking requires issuer/subject mapping and proof of control, never a guessed email match.
- Bootstrap once, transactionally, under a database lock. Create the designated administrator and required organization membership; require an initial password change. Later restarts or changed environment variables must not reset its password or privileges. Ordinary local account creation does not use the current first-user-superadmin shortcut.
- Local sessions use opaque cookie tokens with SHA-256 digests in PostgreSQL, a fixed 24-hour absolute lifetime, and no local refresh-token flow. Use host-only Secure, HttpOnly, SameSite=Lax cookies. Reset tokens expire after one hour; verification tokens after 24 hours. Include atomic token consumption, CSRF/origin checks, and shared login/recovery rate limiting.
- Account enrollment is administrator-created or invitation-based; public registration is disabled. Bootstrap and administrator-created accounts work without SMTP. Extend the existing SMTP service for email flows and provide the planned explicit operator recovery command. Email ownership remains separate from admission of an administrator-provisioned account.
- Disabling revokes account access, sessions, and API keys before remote cleanup. Ordinary logout preserves API keys. Preserve final-superadmin protection under concurrency. Local workspace reset preserves the account and credentials; explicit account deletion removes them.
- Initial local repository connections use manually supplied provider access tokens through one credential API/store. Extend the existing encrypted token storage. Map provider account IDs and hosts locally. A failed provider token refresh must not log the user out of OpenHands.

Important findings from the code investigation:

| File | Finding |
| --- | --- |
| `server/auth/token_manager.py` | Mixes Keycloak login, refresh, user administration, identity lookup, and Git credentials. This is a primary extraction target. |
| `server/auth/saas_user_auth.py` | API-key authentication already works independently of offline Keycloak sessions. Preserve this. Provider token retrieval already has a direct refresh path, but other consumers still use Keycloak. |
| `server/auth/user/user_authorizer.py` | Even the admission-policy interface accepts `KeycloakUserInfo`; replace this coupling with an OpenHands-owned type. |
| `server/routes/auth.py` | Shared admission, invitations, default-org membership, terms, onboarding, and login recording are embedded in the Keycloak callback. Preserve invitation-role precedence over default membership. |
| `storage/user_store.py` and `storage/saas_settings_store.py` | Legacy lookup/hydration calls Keycloak. Storage reads need a local path; legacy hydration belongs in the compatibility service. |
| `storage/user_store.py` | Account creation preserves `User.id == personal Org.id`, grants the first user superadmin, and calls LiteLLM while creating defaults. Bootstrap needs explicit privilege assignment and a local transaction with external provisioning separated. |
| `storage/saas_secrets_store.py` | Drops `provider_tokens` on write. Showing the existing token-entry frontend would not persist Enterprise Git credentials. |
| `storage/org_store.py` | Personal-org deletion can delete the user and depend on Keycloak to recreate it. Local credentials require a separate workspace-reset behavior. |
| `integrations/*`, `integrations/v1_utils.py` | Some background credential consumers and provider-actor lookups still depend on offline tokens/Keycloak. Audit them through the new credential service. |
| `server/middleware.py`, frontend login/linking helpers | Auth policies and redirect construction are coupled to Keycloak cookies/URLs. Use common user context and backend capabilities. |

Start implementation by reading the root `AGENTS.md`, the technical plan, and applicable instructions for any directories being edited. Inspect the current worktree, then install pre-commit hooks before changing code. Establish the interfaces and the chosen library integration, wrap existing Keycloak behavior, and run focused regression tests. Follow the six delivery stages and their completion gates in the technical plan; the complete task includes the local account lifecycle, frontend, repository access, and validation of both modes.

The local release gate is an end-to-end installation with no Keycloak configuration and no reachable Keycloak: bootstrap, password change, additional user creation, repository connection, conversation creation, disabling, and recovery. Also validate API keys, device flow, SDK user/settings access, sandbox credential inheritance, and Enterprise background integration paths. Preserve API-key organization binding and owned-sandbox checks when exposing secrets.

Use SQLite for unit tests and PostgreSQL for migrations, uniqueness, locking, and concurrent bootstrap. Run the repository-required backend pre-commit checks and frontend lint/build for implementation changes. Do not extend testing repetitively after appropriate checks pass. Keep temporary planning artifacts in `.pr/`; production code belongs in the existing application directories.

Scope exclusions are firm: do not investigate or change Helm charts, the separate automation service, or the plugin directory service. Native OIDC/SAML/MFA and customer migration between modes are later work. The planned libraries for later mechanisms are Authlib, `python3-saml`, `pyotp`, and `webauthn`, respectively. Authlib is already a repository dependency. Do not implement those future mechanisms in the initial release.

At handoff, the repository is `/Users/jlaverty/dev/enterprise`, on commit `4e8b65ebf`. No application implementation, migration, or test changes have been made. The only worktree additions are planning documents in `.pr/`. Their Markdown formatting and local links have been checked. There is no active goal object and no subagent work. Follow the session rule against spawning subagents unless explicitly requested.

Pre-commit hooks were installed during planning using `UV_FROZEN=1 INSTALL_PLAYWRIGHT=0 make install-pre-commit-hooks`. This preserved the tracked lockfile; the dependency sync removed an undeclared `testcontainers` package from the local environment. No application tests have run for this planning-only work. Before regenerating `uv.lock` during implementation, use the uv version pinned by the repository as required by `AGENTS.md`.

The user has requested local planning artifacts so far. The next "get started" prompt authorizes implementation. No push, PR publication, or deployment has been requested.
