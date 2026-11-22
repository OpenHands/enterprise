import { openHands } from "../open-hands-axios";

/** A corporate SSO provider the caller may create a SCIM credential for. */
export interface ScimProviderOption {
  id: number;
  title: string;
  protocol: string;
  /** Whether the organization has an active credential for this provider. */
  bound: boolean;
  /** When false, SCIM can neither link nor create accounts through it. */
  allow_match_by_email: boolean;
}

export interface ScimSetup {
  base_url: string;
  /** Whether the caller may bind a provider the organization is not bound to. */
  can_bind_provider: boolean;
  providers: ScimProviderOption[];
}

/** Credential metadata. The token and its hash are never part of it. */
export interface ScimCredential {
  id: string;
  name: string;
  auth_provider_id: number;
  auth_provider_title: string | null;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** The answer to a create: the only time the token is available. */
export interface ScimCredentialCreated extends ScimCredential {
  token: string;
}

const basePath = (orgId: string) => `/api/organizations/${orgId}/scim`;

/**
 * Admin API for an organization's SCIM setup. Needs the `provision_user`
 * permission; binding a new provider needs an instance Super Admin.
 */
export const scimService = {
  getSetup: async (orgId: string) => {
    const { data } = await openHands.get<ScimSetup>(basePath(orgId));
    return data;
  },

  listCredentials: async (orgId: string) => {
    const { data } = await openHands.get<{ items: ScimCredential[] }>(
      `${basePath(orgId)}/credentials`,
    );
    return data.items;
  },

  createCredential: async (
    orgId: string,
    name: string,
    authProviderId: number,
  ) => {
    const { data } = await openHands.post<ScimCredentialCreated>(
      `${basePath(orgId)}/credentials`,
      { name, auth_provider_id: authProviderId },
    );
    return data;
  },

  revokeCredential: async (orgId: string, credentialId: string) => {
    await openHands.delete(`${basePath(orgId)}/credentials/${credentialId}`);
  },
};
