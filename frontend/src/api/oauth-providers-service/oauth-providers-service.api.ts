import { openHands } from "../open-hands-axios";
import type {
  CreateOAuthProviderParams,
  OAuthProvider,
  UpdateOAuthProviderParams,
} from "./oauth-providers.types";

export const oauthProvidersService = {
  listOAuthProviders: async () => {
    const { data } = await openHands.get<OAuthProvider[]>(
      "/api/admin/oauth-providers",
    );
    return data;
  },

  createOAuthProvider: async (params: CreateOAuthProviderParams) => {
    const { data } = await openHands.post<OAuthProvider>(
      "/api/admin/oauth-providers",
      params,
    );
    return data;
  },

  updateOAuthProvider: async ({
    id,
    ...params
  }: { id: number } & UpdateOAuthProviderParams) => {
    const { data } = await openHands.patch<OAuthProvider>(
      `/api/admin/oauth-providers/${id}`,
      params,
    );
    return data;
  },

  deleteOAuthProvider: async ({ id }: { id: number }) => {
    await openHands.delete(`/api/admin/oauth-providers/${id}`);
  },
};
