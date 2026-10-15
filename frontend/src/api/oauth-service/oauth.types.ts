/** How the signed-in session links a git provider. ``provider_id`` is the
 * row to pass to ``POST /oauth/{id}/link``; null for Keycloak, or when the
 * provider isn't configured. */
export interface GitProviderLinkFlow {
  flow: "oauth_v2" | "keycloak";
  provider_id: number | null;
}
