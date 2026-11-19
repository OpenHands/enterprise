# SaaS reviewer sign-in

`GET /oauth/reviewer/login` is an unlisted email/password page for prepared
review accounts. It is not linked from the normal login UI and is excluded
from OpenAPI and search indexing. The URL is not an authentication secret:
explicit account eligibility, IdP verification, CSRF protection and throttling
are enforced on every sign-in. The feature is off by default.

## Identity provider

Use a dedicated confidential Keycloak/OIDC client in a review realm containing
only the operator-provisioned review identities. Enable password grants and
refresh tokens for that client; disable self-registration. Do not change the
production customer realm's MFA or login policy. The operator must configure
this client before enabling the OpenHands page. Its client secret remains in
the encrypted auth-provider configuration, never in the submission ZIP.

Create an enabled `OAuthProviderDetails` provider using the instance-admin
auth-provider management API/UI with:

- `provider_type=enterprise_sso`, HTTPS `token_url` and `userinfo_url`, client ID
  and client secret, and `openid email profile` scopes.
- `is_idp=false`, `allow_registration=false`, `allow_match_by_email=false`.
- No `legacy_keycloak_type` or `named_secret`.

`is_idp=false` keeps this dedicated client out of the normal login chooser.
It still backs the standard OAuth v2 cookie and token refresh path. The
self-hosted development password IDP is not enabled or used.

## Prepare the account

Provision the reviewer as a normal human SaaS user in a dedicated demo
organization, with active membership, verified email and no instance-level
super role. Set that organization as the user's current organization. Prepare
credits, model/sandbox settings and synthetic tasks through normal SaaS
operations. Complete any required terms acceptance as an authorized operator;
the reviewer endpoint does not bypass terms or organization access checks.

Pre-link the dedicated IdP's immutable subject to that user using
`AuthProviderUserStore.link(oauth_provider_id=..., user_id=...,
external_subject_id=..., external_email=...)` in an authorized provisioning
job. Do not infer the subject from an email address, repoint a customer's
identity, or depend on automatic email matching at login. The login route
never creates users, memberships, credentials or identity links.

Configure the application (example identifiers below must be replaced):

```text
ENABLE_REVIEWER_LOGIN=true
REVIEWER_LOGIN_PROVIDER_ID=<dedicated-provider-id>
REVIEWER_LOGIN_ACCOUNTS={"<review-email>":{"user_id":"<review-user-uuid>","org_id":"<demo-org-uuid>"}}
```

Missing/invalid configuration or an unsuitable provider returns 404. Redis
must be available: login fails closed when state or throttling cannot be
checked. The page allows 60 loads per IP per five minutes; password submission
allows 30 attempts per IP and five per email per five minutes. Requests have an
8 KiB body limit. Login forms expire after ten minutes and are single-use.

## Reviewer instructions

1. In the same browser used for ChatGPT, open the submitted SaaS host's
   `/oauth/reviewer/login` URL and enter the supplied email and password.
2. Open the plugin in ChatGPT, choose Connect and approve OpenHands consent.
   No Git account or separately configured MCP connection is needed for
   repository-free tasks.
3. Use the prepared demo organization and tasks. For repository cases, an
   operator must separately connect a restricted demo Git integration.

When a pending authorization URL is available, append it as URL-encoded
`returnTo` to the hidden login URL. Successful login performs document
navigation back to `/external-mcp/consent?request=...`, preserving the opaque
request. Relative same-origin paths only are accepted.

Put the exact login URL, email/password, organization and instructions in the
submission portal's secure reviewer-access fields. No actual passwords should
be committed, logged, shown in the demo recording or included in the ZIP.

The normal IdP access/refresh tokens are encrypted in `oauth_tokens` and the
browser receives the standard HttpOnly `openhands_auth` cookie. Git token
retrieval is not part of this login. Removing an email from the allowlist stops
new reviewer logins; use the existing user-disable/credential-revocation
operations to invalidate existing sessions and MCP grants. Keep the account,
sample data and credits usable during review and subsequent reviews.
