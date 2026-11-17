import type { AuthProviderDetails } from "#/api/auth-providers-service/auth-providers.types";

/**
 * Whether a provider is a single sign-on identity provider: SAML, or a direct
 * OIDC/OAuth identity provider such as Okta or Google. Git providers don't
 * count. The server makes the same check (``server/licensing/sso.py``).
 */
export function isSsoProvider(details: AuthProviderDetails): boolean {
  if (details.kind === "SamlProviderDetails") return true;
  return (
    details.kind === "OAuthProviderDetails" &&
    details.provider_type === "enterprise_sso" &&
    details.legacy_keycloak_type === null
  );
}
