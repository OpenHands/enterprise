# The SaaS Server

The SaaS/enterprise server (`saas_server.py` plus the `server/`, `storage/`, `integrations/`, `sync/`, `analytics/`
and `utils/` packages at the repository root) is what [OpenHands Cloud](https://github.com/OpenHands/OpenHands-Cloud/)
and self-hosted OpenHands Enterprise installs run. The public OpenHands Cloud is available at
[app.all-hands.dev](https://app.all-hands.dev).

The licence for this repository is described in the top-level [README](../../README.md).

## Extension of the OpenHands app server

The SaaS server builds on top of the OpenHands app server (`openhands/`), extending its functionality. The two are
entangled in two ways:

- Enterprise stacks on top of OpenHands. For example, the middleware in `server/` is stacked right on top of the middlewares in `openhands/`. In `SAAS`, the middleware from BOTH layers will be present and running (which can sometimes cause conflicts)

- Enterprise overrides the implementation in OpenHands (only one is present at a time). For example, `server.config.SaaSServerConfig` overrides `ServerConfig` (`openhands/app_server/server_config/server_config.py`). This is done through dynamic imports: `OPENHANDS_CONFIG_CLS` and the `OH_*_KIND` environment variables name the class to load (`saas_server.py` sets `OPENHANDS_CONFIG_CLS=server.config.SaaSServerConfig`; the Docker image defaults the `OH_*_KIND` variables)

Key areas that change on `SAAS` are

- Authentication
- User settings
- etc

### Authentication

| Aspect                    | OpenHands                                              | Enterprise                                                                                                                                 |
| ------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| **Authentication Method** | User adds a personal access token (PAT) through the UI | User performs OAuth through the UI. The GitHub app provides a short-lived access token and refresh token                            |
| **Token Storage**         | PAT is stored in **Settings**                          | Token is stored in **TokenManager** (a file store in our backend)                                                             |
| **Authenticated status**  | We simply check if token exists in `Settings`          | We issue a signed cookie with `github_user_id` during OAuth, so subsequent requests with the cookie can be considered authenticated |

Authentication in enterprise uses Keycloak (see `server/auth/constants.py` for the `KEYCLOAK_*` configuration).

### GitHub Service

The github service is responsible for interacting with Github APIs. As a consequence, it uses the user's token and refreshes it if need be

| Aspect                    | OpenHands                               | Enterprise                                            |
| ------------------------- | -------------------------------------- | ---------------------------------------------- |
| **Class used**            | `GitHubService`                        | `SaaSGitHubService`                            |
| **Token used**            | User's PAT fetched from `Settings`     | User's token fetched from `TokenManager` |
| **Refresh functionality** | **N/A**; user provides PAT for the app | Uses the `TokenManager` to refresh       |

NOTE: The `SaaSGitHubService` interacts with Keycloak for authentication.

### Email delivery (SMTP for invitations & budget alerts)

Organization invitation emails and budget alert emails are sent via SMTP when configured. If
`SMTP_HOST` is unset, invitations are still created but no email is sent (the UI surfaces
copyable invite links instead).

| Env var | Purpose | Default |
| --- | --- | --- |
| `SMTP_HOST` | SMTP server hostname | (required) |
| `SMTP_PORT` | SMTP server port | `587` |
| `SMTP_USERNAME` | SMTP auth username | empty |
| `SMTP_PASSWORD` | SMTP auth password | empty |
| `SMTP_FROM_EMAIL` | Sender address | `OpenHands <no-reply@openhands.dev>` |
| `SMTP_USE_SSL` | Use implicit TLS/SSL | `false` |
| `SMTP_USE_TLS` | StartTLS upgrade (ignored if SSL) | `true` |


# Areas that are BRITTLE!

## User ID vs User Token

- In OpenHands, the entire app revolves around the GitHub token the user sets. `openhands/server` uses `request.state.github_token` for the entire app
- On Enterprise, the entire APP resolves around the Github User ID. This is because the cookie sets it, so `openhands/server` AND `server/` depend on it and completely ignore `request.state.github_token` (token is fetched from `GithubTokenManager` instead)

Note that introducing GitHub User ID in OpenHands, for instance, will cause large breakages.
