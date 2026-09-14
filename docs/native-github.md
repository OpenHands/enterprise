# GitHub connections with native authentication

With `ENABLE_KEYCLOAK=false`, users connect GitHub from Settings → Integrations after signing in with their password or SAML identity. Connecting or disconnecting GitHub preserves the application login.

Apply migrations before starting the updated application:

```sh
uv run alembic upgrade head
```

Personal access tokens are enabled by default with `NATIVE_GIT_GITHUB_MANUAL_ENABLED=true`. A user selects an approved host, enters a token, and saves the connection. The application verifies the GitHub identity before storing the credential. Editing, canceling, or saving clears the token from the form.

## Approved GitHub hosts

Set `NATIVE_GIT_GITHUB_HOSTS` to a comma-separated list of approved HTTPS hostnames. It defaults to `GITHUB_HOST` when supplied, otherwise `github.com`. The first hostname is the default. Ports, paths, query strings, fragments, and credentials are rejected.

For GitHub Enterprise Server, supply its hostname. The adapter uses that host's `/api/v3` API. Users can select only configured hosts; browser input cannot select an arbitrary API endpoint.

`OPENHANDS_GITHUB_SERVICE_CLS` defaults to `integrations.github.github_service.SaaSGitHubService` in native mode. A custom adapter must declare native authentication support.

## OAuth connections

Set `NATIVE_GIT_GITHUB_OAUTH_ENABLED=true` and supply `GITHUB_APP_CLIENT_ID` and secret `GITHUB_APP_CLIENT_SECRET`. OAuth is disabled by default. Both GitHub enable toggles accept case-insensitive `true` and `1`.

Register this callback URL with the GitHub OAuth application or GitHub App:

```text
${NATIVE_AUTH_APP_ORIGIN}/oauth/git/github/callback
```

The Connect action uses the server-provided authorization URL. OAuth state is bound to the current browser session, account, provider, and approved host. Expired or mismatched callbacks cannot replace a connection.

## GitHub App installation and webhooks

Supply secret `GITHUB_APP_PRIVATE_KEY` and `GITHUB_APP_ID` or `GITHUB_APP_CLIENT_ID` to enable the installation action and native GitHub webhook workers. Configure `GITHUB_APP_WEBHOOK_SECRET` for webhook signature verification. OAuth credentials alone do not start webhook workers.

The installation action retrieves the App's installation URL from GitHub. User authorization revocation disconnects the matching GitHub identity while preserving the application session.

Credentials and refresh tokens are encrypted at rest with the installation's JWT encryption key and excluded from public connection responses. Preserve that key across restarts and restores. If GitHub rejects a credential, Settings displays a reconnect action; temporary provider failures preserve the stored connection.
