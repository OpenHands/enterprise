# Native SAML sign-in

Configure SAML on an existing [native authentication installation](native-authentication.md) to offer an external identity provider alongside email and password. Register the application with your identity provider and mount its signing certificates into every application worker.

Set `NATIVE_SAML_ENABLED=true` and configure:

| Setting | Value or default |
| --- | --- |
| `NATIVE_SAML_IDP_ENTITY_ID` | Required identity provider issuer. |
| `NATIVE_SAML_IDP_SSO_URL` | Required HTTPS sign-in endpoint. |
| `NATIVE_SAML_IDP_CERT_FILE` | Required PEM trust bundle file. |
| `NATIVE_SAML_CONNECTION_ID` | Defaults to `saml`. Keep this stable. |
| `NATIVE_SAML_DISPLAY_NAME` | Defaults to `SSO`. |
| `NATIVE_SAML_EMAIL_ATTRIBUTE` | Defaults to `email`. |
| `NATIVE_SAML_SP_ENTITY_ID` | Defaults to `${NATIVE_AUTH_APP_ORIGIN}/api/auth/saml/metadata`. |
| `NATIVE_SAML_JIT_ENABLED` | Defaults to `true`. |
| `NATIVE_SAML_SIGN_REQUESTS` | Defaults to `false`. |
| `NATIVE_SAML_REQUIRE_ENCRYPTED_ASSERTIONS` | Defaults to `false`. |

SAML is disabled by default. Boolean settings accept case-insensitive `true`, `1`, `false`, or `0`. Configure the identity provider's assertion consumer URL as `${NATIVE_AUTH_APP_ORIGIN}/api/auth/saml/acs`. Application metadata is available at `/api/auth/saml/metadata`.

Use a PEM trust bundle containing one through eight RSA certificates with keys of at least 2048 bits. For signed requests or encrypted assertions, set both `NATIVE_SAML_SP_CERT_FILE` and the secret `NATIVE_SAML_SP_KEY_FILE` to mounted files. The private key must be unencrypted PEM and match the certificate. Restart workers after changing trust files, and keep their configuration consistent.

The first permitted sign-in can create an ordinary account when just-in-time account creation is enabled. An existing email address requires explicit linking from Settings after recent authentication. Setup invitations retain their intended email and organization scope. Users with SAML-only accounts sign in through the configured identity provider.

Keep the connection ID and issuer stable to preserve identity. Disabling or replacing the configured connection prevents its existing identities from authenticating. Password accounts remain available for administration and recovery. Never log SAML assertions or private keys.
