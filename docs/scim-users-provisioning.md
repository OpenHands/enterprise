# SCIM user provisioning (users only)

An identity provider can add and remove the members of one organization through
SCIM 2.0. Set `SCIM_ENABLED=true` (or `1`) to enable `/scim/v2` and the
management API. The SCIM provisioning page under **Super Admin > Organizations**
also requires the Super Admin dashboard.

Only `/Users` is implemented. There are no group routes and no group push:
organization roles other than `member` are managed in the app.

## Who can manage SCIM

Only instance Super Admins can view SCIM setup and credential status, connect a
provider, rotate its credentials, or revoke them. Select the team organization
in **Super Admin > Organizations**, then open **SCIM provisioning** from its
row menu. A Super Admin does not need to join the organization.

Each credential connects one corporate SSO provider to one team organization.
Through that connection, the provider may link directory users to existing
OpenHands accounts and sync that organization's ordinary membership. Only enabled
corporate SSO providers (SAML or direct OIDC) are eligible.

A SCIM credential works only on `/scim/v2`. Use OpenHands API credentials for
the separate Super Admin management endpoints.

## Set up

1. Under **Super Admin > Organizations**, open **SCIM provisioning** from
   the team organization’s row menu.
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

Revocation takes effect on the next request and cannot be undone. Revoking the
last active credential stops that provider from syncing until its configuration
is updated to use a new credential. Existing memberships stay in place.

## Microsoft Entra ID

For a non-gallery app, Microsoft Entra ID runs incremental provisioning about
every 40 minutes after the initial sync. The initial sync can take longer.
Allow for the next provisioning cycle after removing an assignment, and check
**Provisioning logs** to confirm delivery. See [Microsoft's SCIM provisioning
schedule][entra-scim].

In an isolated Entra test tenant, we provisioned a user and verified that both
on-demand provisioning and automatic sync could deactivate and reactivate the
same OpenHands account. The live lifecycle tests used the SCIM 2.0 compliance
flag (`aadOptscim062020`). The legacy `active` values are covered by automated
contract tests.

- Accepted: `POST /Users`, `GET /Users?filter=userName eq "..."`, and a
  deactivation or reactivation sent as
  `{"op": "Replace", "path": "active", "value": false}` (or `true`).
- Accepted: the same with `active` sent as the exact string `"False"` or
  `"True"`, which is what Entra sends without its SCIM 2.0 compliance flag
  (`aadOptscim062020`). Only with `"path": "active"`; any other string, a
  number or `null` is a `400`.
- Accepted: with the compliance flag, Entra can put
  `{"op": "add", "path": "name.formatted", "value": "<given> <family>"}` ahead
  of the `active` operation. `name.formatted` is checked to be a string of at
  most 255 characters and then discarded: it is not stored or returned, and
  never affects the account. It is accepted only in a `PATCH` that also sets
  `active`.
- Rejected with `400`, changing nothing: a `PATCH` with any other operation or
  attribute, including `name.formatted` on its own, `roles`, `title`, other
  `name` or `emails` paths, or enterprise extension attributes. Every
  operation is checked before any change is made. Entra updates profile
  attributes with `PATCH`, so those updates will be reported as errors until
  this is implemented. `PUT` updates work.

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
