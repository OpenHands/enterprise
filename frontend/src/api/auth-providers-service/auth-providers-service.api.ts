import { openHands } from "../open-hands-axios";
import type {
  AuthProvider,
  AuthProviderUserLink,
  AuthProviderUserLinkPage,
  CreateAuthProviderParams,
  PublicIdpProvider,
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

  /** Public, unauthenticated list of configured login IDPs (see
   * ``server.routes.oauth_v2.oauth_v2_list_idp_providers``) -- backs the
   * "Let's Get Started" login screen's provider buttons when
   * ``ENABLE_OAUTH_V2_LOGIN`` is on. Never requires a session and never
   * carries secrets. */
  listPublicIdpProviders: async () => {
    const { data } = await openHands.get<PublicIdpProvider[]>(
      "/oauth/idp-providers",
    );
    return data;
  },

  /** Paginated "User Links" (``auth_provider_users``) for one provider --
   * backs ``/super-admin/auth-providers/{provider_id}/users``. ``page`` is
   * 1-based; converted to the backend's offset ``page_id`` here, mirroring
   * ``adminService.getAllUsers``. */
  listAuthProviderUsers: async ({
    providerId,
    page = 1,
    limit = 10,
    q,
  }: {
    providerId: number;
    page?: number;
    limit?: number;
    q?: string;
  }) => {
    const params = new URLSearchParams();
    const offset = (page - 1) * limit;
    params.set("page_id", String(offset));
    params.set("limit", String(limit));
    if (q) {
      params.set("q", q);
    }
    const { data } = await openHands.get<AuthProviderUserLinkPage>(
      `/api/admin/oauth-providers/${providerId}/users?${params.toString()}`,
    );
    return data;
  },

  /** Re-points a "User Link" to a different, already-existing user (see
   * ``server.routes.oauth_providers_admin.update_auth_provider_user``). */
  updateAuthProviderUser: async ({
    providerId,
    linkId,
    userId,
  }: {
    providerId: number;
    linkId: number;
    userId: string;
  }) => {
    const { data } = await openHands.patch<AuthProviderUserLink>(
      `/api/admin/oauth-providers/${providerId}/users/${linkId}`,
      { user_id: userId },
    );
    return data;
  },
};
