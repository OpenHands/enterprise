import { generateAuthUrl } from "./generate-auth-url";
import { getLoginMethod } from "./local-storage";
import { Provider } from "#/types/settings";

/**
 * Generates the Keycloak URL that links a git provider to the already
 * signed-in user (Keycloak application-initiated action `idp_link`).
 * The backend callback reads `link_provider` from the OAuth state, stores the
 * provider tokens and redirects back to `redirect_url`.
 * @param provider The git provider to link (e.g., "github", "gitlab", "bitbucket")
 * @param requestUrl The URL of the current page, returned to after linking
 * @param authUrl Optional Keycloak host from the web client config
 * @returns The URL to redirect to for linking
 */
export const generateIdpLinkUrl = (
  provider: Provider,
  requestUrl: URL,
  authUrl?: string | null,
) => {
  // Hint Keycloak towards the method the user signed in with, so that a forced
  // re-authentication goes to their own IdP rather than the provider being linked.
  const loginMethod = getLoginMethod();
  const url = new URL(
    generateAuthUrl(loginMethod ?? provider, requestUrl, authUrl),
  );
  if (!loginMethod) {
    url.searchParams.delete("kc_idp_hint");
  }
  url.searchParams.set("kc_action", `idp_link:${provider}`);
  url.searchParams.set(
    "state",
    btoa(
      JSON.stringify({
        redirect_url: `${requestUrl.origin}${requestUrl.pathname}`,
        link_provider: provider,
      }),
    ),
  );
  return url.toString();
};
