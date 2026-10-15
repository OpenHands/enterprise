import { openHands } from "../open-hands-axios";
import { Provider } from "#/types/settings";
import type { GitProviderLinkFlow } from "./oauth.types";

/** The OAuth v2 routes (``server/routes/oauth_v2.py``), used where there
 * is no Keycloak, e.g. installs on the integrated IDP. */
const OAuthService = {
  getLinkFlow: async (provider: Provider): Promise<GitProviderLinkFlow> => {
    const { data } = await openHands.get<GitProviderLinkFlow>(
      "/oauth/link-flow",
      { params: { provider } },
    );
    return data;
  },

  /** Returns the URL that starts linking ``providerId`` to the signed-in
   * user; the browser is sent there and comes back to the integrations page. */
  startLink: async (providerId: number): Promise<string> => {
    const { data } = await openHands.post<{ start_url: string }>(
      `/oauth/${providerId}/link`,
    );
    return data.start_url;
  },
};

export default OAuthService;
