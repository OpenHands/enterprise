import { openHands } from "../open-hands-axios";

export interface SuperAdminApiAdmin {
  user_id: string;
  email: string | null;
}

export interface SuperAdminApiOrg {
  id: string;
  name: string;
  contact_email: string | null;
  contact_name: string | null;
  member_count: number;
  is_personal: boolean;
  status: "active" | "suspended";
}

export interface SuperAdminApiMembership {
  org_id: string;
  org_name: string;
  role: string;
  status: string | null;
}

export interface SuperAdminApiUser {
  user_id: string;
  email: string | null;
  name: string | null;
  memberships: SuperAdminApiMembership[];
  status: "active" | "inactive";
}

export interface ProvisionUserRequest {
  email: string;
  password?: string;
  api_key_name?: string;
  role?: "member" | "admin" | "owner";
  reissue_api_key?: boolean;
}

export interface ProvisionUserResponse {
  email: string;
  password: string | null;
  api_key: string;
  user_id: string;
  org_id: string;
  role: string;
  created: boolean;
  action: "created" | "added_to_org" | "reprovisioned";
}

export const superAdminService = {
  listSuperAdmins: async () => {
    const { data } = await openHands.get<{
      super_admins: SuperAdminApiAdmin[];
    }>("/api/admin/super-admins");
    return data.super_admins;
  },

  grantSuperAdmin: async ({ email }: { email: string }) => {
    const { data } = await openHands.post<SuperAdminApiAdmin>(
      "/api/admin/super-admins",
      { email },
    );
    return data;
  },

  revokeSuperAdmin: async ({ userId }: { userId: string }) => {
    const { data } = await openHands.delete<SuperAdminApiAdmin>(
      `/api/admin/super-admins/${userId}`,
    );
    return data;
  },

  listOrganizations: async () => {
    const { data } = await openHands.get<{
      organizations: SuperAdminApiOrg[];
    }>("/api/admin/organizations");
    return data.organizations;
  },

  deleteOrganization: async ({ orgId }: { orgId: string }) => {
    await openHands.delete(`/api/admin/organizations/${orgId}`);
  },

  updateOrganizationStatus: async ({
    orgId,
    status,
  }: {
    orgId: string;
    status: "active" | "suspended";
  }) => {
    const { data } = await openHands.patch<SuperAdminApiOrg>(
      `/api/admin/organizations/${orgId}`,
      { status },
    );
    return data;
  },

  listUsers: async () => {
    const { data } = await openHands.get<{ users: SuperAdminApiUser[] }>(
      "/api/admin/users",
    );
    return data.users;
  },

  updateUserStatus: async ({
    userId,
    status,
  }: {
    userId: string;
    status: "active" | "inactive";
  }) => {
    const { data } = await openHands.patch<SuperAdminApiUser>(
      `/api/admin/users/${userId}`,
      { status },
    );
    return data;
  },

  removeUser: async ({ userId }: { userId: string }) => {
    const { data } = await openHands.delete<{
      message: string;
      user_id: string;
      removed_org_ids: string[];
    }>(`/api/admin/users/${userId}`);
    return data;
  },

  provisionUser: async ({
    orgId,
    payload,
  }: {
    orgId: string;
    payload: ProvisionUserRequest;
  }) => {
    const { data } = await openHands.post<ProvisionUserResponse>(
      "/api/organizations/provision-user",
      payload,
      { headers: { "X-Org-Id": orgId } },
    );
    return data;
  },
};
