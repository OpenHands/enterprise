import { openHands } from "../open-hands-axios";

export interface OAuthV2GitProvider {
  id: number;
  title: string;
  icon: string | null;
  color: string;
  provider_type: string;
  connected: boolean;
}

export const oauthV2Service = {
  /** Git providers defined in ``auth_providers`` plus whether the current
   * user has linked each one (an ``oauth_tokens`` row). */
  listGitProviders: async () => {
    const { data } = await openHands.get<OAuthV2GitProvider[]>(
      "/oauth/git-providers",
    );
    return data;
  },

  /** Returns the URL that starts the provider's OAuth flow in link mode. */
  startLinkGitProvider: async (providerId: number) => {
    const { data } = await openHands.post<{ start_url: string }>(
      `/oauth/${providerId}/link`,
    );
    return data.start_url;
  },

  unlinkGitProvider: async (providerId: number) => {
    await openHands.delete(`/oauth/${providerId}`);
  },
};
