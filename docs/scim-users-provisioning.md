# SCIM user provisioning (users only)

An identity provider can add and remove the members of one organization through
SCIM 2.0. The feature is off unless the server runs with `SCIM_ENABLED=true`
(or `1`). While it is off, `/scim/v2`, the admin API and the settings page do
not exist.

Only `/Users` is implemented. There are no group routes and no group push:
organization roles other than `member` are managed in the app.

## Who can do what

| Action | Org owner / admin | Instance Super Admin |
|---|---|---|
| See the SCIM base URL and the credential list | yes | yes |
| Revoke a credential | yes | yes |
| Create a credential for a provider the organization already has an active credential for (rotation) | yes | yes |
| Create the first credential for a provider (connect the organization to it) | no | yes |

SSO providers are configured once for the whole instance, and nothing records
which organization a provider's directory speaks for. Connecting one to an
organization lets that directory's email addresses link to existing accounts,
so the first credential needs a Super Admin. It is created for the organization
selected in the app: open the organization from **Super Admin > Organizations**
(the dashboard asks you to grant yourself access if you are not a member) and go
to **Settings > SCIM provisioning**. The API takes the organization in the path
and does not need the membership.

Only enabled corporate SSO providers (SAML, or a direct OIDC provider) can be
connected. The password login and git providers cannot.

A SCIM credential is not an API key: it works on `/scim/v2` only, and an API
key does not work there.

## Set up

1. As a Super Admin, open the organization and go to **Settings > SCIM
   provisioning**.
2. Copy the **SCIM base URL** (`https://<your host>/scim/v2`).
3. Select **Create credential**, name it after the identity provider app that
   will hold it, and choose the SSO provider.
4. Copy the token. It is shown once; the server stores only a hash.

## When access changes take effect

Your identity provider controls when it sends provisioning changes. Once
OpenHands processes a deactivation, it removes the user's membership and
personal API keys for that organization. The user's next request to the
organization is denied, including requests from an existing session. If access
hasn't changed, check the provider's provisioning logs to see whether it sent
the deactivation and whether the request succeeded.

## Okta

In the Okta admin console, on a SCIM 2.0 app integration (or the
**Provisioning** tab of your OIDC/SAML app once SCIM provisioning is enabled
for it):

| Okta setting | Value |
|---|---|
| SCIM connector base URL | the SCIM base URL |
| Unique identifier field for users | `userName` |
| Supported provisioning actions | Push New Users, Push Profile Updates |
| Authentication mode | HTTP Header |
| Authorization | the token (Okta sends it as `Bearer <token>`) |

Leave **Push Groups** and **Import Groups** off. Then, under **Provisioning >
To App**, enable **Create Users**, **Update User Attributes** and **Deactivate
Users**. The Okta username must be the person's email address, or the app must
send a primary email.

Assigning a person to the app creates their membership. With **Deactivate Users**
enabled, unassigning them or deactivating their Okta account triggers a SCIM
update. Okta delivered the update within seconds in our test. Your delivery
time may vary. An Okta suspension alone sends no SCIM deactivation.
Assigning the person again restores membership. They must create new API keys.
See [Okta's SCIM deactivation behavior][okta-scim].

## Rotate a credential

1. Create a new credential for the same provider.
2. Replace the token in the identity provider and run its connection test.
3. Reload the page and check that the new credential shows **Last used**.
4. Revoke the old credential.

Revocation takes effect on the next request and cannot be undone. If you
revoke the last active credential for a provider, a Super Admin has to create
the next one.

## Microsoft Entra ID

For a non-gallery app, Microsoft Entra ID runs incremental provisioning about
every 40 minutes after the initial sync. The initial sync can take longer.
Allow for the next provisioning cycle after removing an assignment, and check
**Provisioning logs** to confirm delivery. See [Microsoft's SCIM provisioning
schedule][entra-scim].

Not tested against a live Entra tenant. From the request shapes Microsoft
documents, and the contract tests in
`tests/unit/server/routes/test_scim_users.py`:

- Accepted: `POST /Users`, `GET /Users?filter=userName eq "..."`, and a
  deactivation sent as `{"op": "Replace", "path": "active", "value": false}`.
- Rejected with `400`: `active` sent as the string `"False"`. Entra sends a
  JSON boolean only when its SCIM 2.0 compliance flag is set on the tenant URL.
- Rejected with `400`: a `PATCH` that changes anything other than `active`.
  Entra updates profile attributes with `PATCH`, so those updates will be
  reported as errors until this is implemented. `PUT` updates work.

## What SCIM does to accounts

- It grants and removes the `member` role only. Owners, admins, service
  accounts and Super Admins are never adopted or removed: the request gets a
  `409` and nothing changes.
- A person is linked to an existing account by email, only when exactly one
  account has that email and the provider allows matching by email. This is the
  same rule sign-in through that provider uses, with the same consequence: the
  provider's emails are trusted, whether or not the existing account ever
  verified its address.
- A record's email cannot be changed after it is created.
- A deactivated person cannot be added back by an invitation, a group mapping
  or an admin until the identity provider reactivates them. This holds while
  `SCIM_ENABLED` is off.

## Limits

- `DELETE`, bulk, sorting, ETags and password changes are not implemented.
- Filters: `userName eq` and `externalId eq` only.
- At most 10 active credentials per organization.

[okta-scim]: https://developer.okta.com/docs/api/openapi/okta-scim/guides/scim-20#delete-users
[entra-scim]: https://learn.microsoft.com/en-us/entra/identity/app-provisioning/use-scim-to-provision-users-and-groups#integrate-your-scim-endpoint-with-the-microsoft-entra-provisioning-service
