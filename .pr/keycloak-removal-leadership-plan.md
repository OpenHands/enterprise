# Make Keycloak optional in OpenHands Enterprise

OpenHands Enterprise will include email/password authentication so new self-hosted installations can start without deploying Keycloak. OpenHands will bootstrap an administrator and manage accounts directly. Existing installations will retain their authentication behavior. Customers requiring SAML or OIDC authentication will continue to install Keycloak and enable its integration until OpenHands supports those mechanisms natively.

The objective is to remove a recurring installation blocker and reduce the time from installation to first use. This work also establishes clear ownership of accounts and permissions within OpenHands, preparing us to retire Keycloak later.

The implementation details and supporting code investigation are preserved in the [technical plan](keycloak-removal-technical-plan.md).

The first release has a clear customer experience:

| Customer situation | Result |
| --- | --- |
| New installation | Email/password authentication is the default. Keycloak is disabled. |
| Initial administrator | The installation creates the designated administrator from email and password environment variables. The administrator changes the initial password at first login. |
| Additional users | Administrators create accounts and manage invitations. Public registration is disabled. |
| Existing installation | Existing Keycloak authentication, accounts, permissions, and integrations continue to work. |
| Installation requiring SAML or OIDC | The customer installs Keycloak and enables the integration. |

Bootstrap credentials come only from `OH_BOOTSTRAP_ADMIN_EMAIL` and `OH_BOOTSTRAP_ADMIN_PASSWORD`. Bootstrap runs once. Restarting the application or changing these variables does not reset an existing administrator's password or privileges.

The engineering direction is to give OpenHands one account-management interface and keep authentication implementations behind it. The application will use the same account identity and permission checks regardless of how a user authenticates. We will use FastAPI Users for local password authentication. Its maintainers continue security and dependency maintenance, which fits this bounded responsibility. [FastAPI Users](https://github.com/fastapi-users/fastapi-users)

The work will proceed through four delivery milestones:

1. **Separate Keycloak from application account management.** Introduce the shared interfaces and move existing Keycloak behavior behind an adapter. Preserve current customer behavior while changing the internal structure.
2. **Deliver complete local account access.** Add email/password login, administrator bootstrap, sessions, account provisioning, password changes, recovery, and account disabling. Initial setup and administrator-created accounts work without configuring an email server.
3. **Make local accounts useful throughout Enterprise.** Connect the login and account-management screens to the new interfaces. Support repository access through provider access tokens and remove hidden Keycloak dependencies from Enterprise integration paths.
4. **Validate both installation paths and enable the new default.** Test fresh installations without Keycloak and upgrades of existing Keycloak installations. Release local authentication as the default for fresh installations after both paths pass.

The main engineering risk is the existing coupling outside login. Keycloak currently participates in account provisioning, profile lookup, repository credentials, and recovery after workspace deletion. The technical plan addresses those paths explicitly. Supporting email/password login alone would leave new installations blocked elsewhere in the product.

The release is complete when:

- A fresh installation can bootstrap its administrator, create another user, connect a repository, and start a conversation with Keycloak absent.
- Browser login, account management, API keys, SDK access, and supported Enterprise integration paths work with local accounts.
- Existing installations retain their identities, organization memberships, permissions, and supported Keycloak authentication mechanisms.
- Restarting or upgrading cannot silently change the authentication mode or recreate administrator credentials.
- The documented first-install experience uses one prescribed configuration path.

Measure the result using installation completion rates, time to first successful login and conversation, and support incidents attributed to authentication setup. Capture a baseline before release so the improvement can be evaluated.

Native OIDC support will follow the initial release, followed by native SAML support and an explicit migration process for existing customers. Keycloak deprecation will follow functional coverage and a validated migration path. Changing a feature flag will not automatically migrate an existing customer's identities or passwords.

This plan covers OpenHands Enterprise. Helm charts and the separate automation and plugin directory services are outside its scope.
