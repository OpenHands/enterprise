# Operating Enterprise authentication

Use local password authentication for a new installation. Keep Keycloak for an existing Keycloak installation or when you need its SAML/OIDC sign-in integrations.

## Select the installation mode

Run database migrations before starting the application. Startup records the authentication mode in PostgreSQL and initializes local authentication before accepting sign-ins.

| Installation state | `KEYCLOAK_ENABLED` | Behavior |
| --- | --- | --- |
| Fresh, without legacy identity data or explicit Keycloak configuration | Unset | Initializes local authentication. |
| Existing recorded mode | Unset | Retains that mode. |
| Legacy installation with identity data or explicit Keycloak configuration | Unset | Retains Keycloak and validates its configuration. |
| New Keycloak installation | `true` or `1` | Uses Keycloak and validates its configuration. |
| New local installation without legacy Keycloak state | `false` or `0` | Uses local authentication. |

Boolean values are case-insensitive. Other values are rejected. Restore the recorded mode if startup reports a conflict. Changing this flag does not import Keycloak passwords, enroll existing accounts, or migrate a populated installation.

## Initialize the local administrator

Provide these two environment variables through your deployment's secret management:

```text
OH_BOOTSTRAP_ADMIN_EMAIL
OH_BOOTSTRAP_ADMIN_PASSWORD
```

Use an initial password containing 15 to 1024 characters. Spaces and Unicode are accepted and preserved. Sign in with that email and password, then replace the initial password when prompted.

Bootstrap runs once. After it succeeds, remove the bootstrap values from the application environment. Changing them or restarting the application does not reset the administrator's password or restore a removed role. SMTP is not required for bootstrap or administrator-created accounts.

Keep PostgreSQL data and the existing `JWT_SECRET` persistent, and use the same signing/encryption configuration across application replicas. Configure shared Redis for authentication limits. Password and recovery requests return a temporary availability error when their shared limiter is unavailable. Serve the browser application over HTTPS so secure authentication cookies are sent.

## Manage accounts and repository access

Instance administrators can create accounts in the administration UI. New accounts receive an initial password that must be changed at first sign-in. Organization owners and administrators can use the existing provisioning API for team membership and API-key issuance. Repeating provisioning for an existing account preserves its password; keys are replaced only when explicitly requested.

There is no public registration. Invitations allow new users to choose a password through the invitation enrollment page. Existing account holders sign in to accept their invitation. Automatic acceptance of invitations addressed to an email requires verified ownership of that address.

Configure the LLM in Settings before starting conversations. Connect Git providers in Settings using provider access tokens. Repository access is independent of the password used to sign in. Reconnect an expired or revoked provider token without resetting the OpenHands account.

Disabling an account revokes its browser sessions and API keys. Ordinary logout preserves API keys. If disable or delete returns a cleanup warning, access is already denied locally; repeat the same operation to finish remote cleanup. Deletion retains the disabled account until cleanup succeeds. The final active instance administrator cannot be demoted, disabled, or deleted.

Resetting a local personal workspace clears workspace data while preserving the account and password. Use account deletion to remove the identity and its credential.

## Configure email and recover access

Configure the existing SMTP service to deliver invitations, email verification, and self-service password reset. Set `WEB_HOST` explicitly to the public HTTPS origin, for example `https://openhands.example.com`. Do not include a path. Email recovery remains unavailable until SMTP and this public origin are configured.

Users can request a reset on the sign-in page. Reset links expire after one hour; verification links expire after 24 hours. Both are single-use. Completing a password reset revokes existing browser sessions. Changing a login email requires verification of the replacement address.

When email recovery is unavailable, run the recovery command in the application environment with its normal database and secret configuration:

```bash
python -m server.auth.local.recover_admin
```

Enter the email of an existing, active local instance administrator and the new password when prompted. The command revokes browser sessions and replaces that account's password. It does not create an administrator, enable a disabled account, or change roles. Bootstrap variables are not a recovery mechanism.

Continue using the existing Keycloak recovery and SSO configuration for Keycloak installations. Native SAML/OIDC login, MFA, and migration between authentication modes are separate capabilities and are not provided by changing the mode flag.
