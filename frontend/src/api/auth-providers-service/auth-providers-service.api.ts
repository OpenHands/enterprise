import { openHands } from "../open-hands-axios";
import type {
  AuthProvider,
  CreateAuthProviderParams,
  SamlIdpMetadata,
  UpdateAuthProviderParams,
} from "./auth-providers.types";

// The REST path itself stays `/api/admin/oauth-providers` -- it is the
// backend's route, unrelated to this rename.
export const authProvidersService = {
  listAuthProviders: async () => {
    const { data } = await openHands.get<AuthProvider[]>(
      "/api/admin/oauth-providers",
    );
    return data;
  },

  createAuthProvider: async (params: CreateAuthProviderParams) => {
    const { data } = await openHands.post<AuthProvider>(
      "/api/admin/oauth-providers",
      params,
    );
    return data;
  },

  updateAuthProvider: async ({
    id,
    ...params
  }: { id: number } & UpdateAuthProviderParams) => {
    const { data } = await openHands.patch<AuthProvider>(
      `/api/admin/oauth-providers/${id}`,
      params,
    );
    return data;
  },

  deleteAuthProvider: async ({ id }: { id: number }) => {
    await openHands.delete(`/api/admin/oauth-providers/${id}`);
  },

  /** Fetches and parses a SAML IdP's metadata document server-side (see
   * ``server.routes.oauth_providers_admin.preview_saml_metadata``), to
   * prefill the rest of the SAML form from its "Metadata URL" field. Does
   * not persist anything. */
  previewSamlMetadata: async (metadataUrl: string) => {
    const { data } = await openHands.post<SamlIdpMetadata>(
      "/api/admin/oauth-providers/saml-metadata-preview",
      { metadata_url: metadataUrl },
    );
    return data;
  },
};
