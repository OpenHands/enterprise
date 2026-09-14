# MockSAML fixture

The Compose overlay builds [Ory MockSAML](https://github.com/ory/mocksaml) at commit
`ee99e97dfef4217ccfd008910fdc95a68276d6ec` with its own package lockfile and
`@boxyhq/saml20` version `1.12.2`. The Node base image is pinned by digest.

`patch.cjs` changes the generated NameID to the persistent format and uses
MockSAML's `claims.raw.id`, the SHA-256 digest of the selected email. The patch runs
before building the app and before assertions are signed. It also updates IdP
metadata to advertise persistent NameIDs and unsigned authentication requests,
which match the fixture's behavior. A dependency version or source mismatch
fails the build.

This fixture does not model changing a user's email while preserving their
identity. Choose a consistent test email when testing an existing account.
Each request presents a simulated login form and submission produces a fresh
`AuthnInstant`. MockSAML does not otherwise implement `ForceAuthn` semantics.

These adaptations are confined to the test IdP. Enterprise continues to validate
the signed assertion, persistent identity, audience, browser transaction, and
replay protection. Signing keys are supplied at runtime and are absent from the
image build contexts.
