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

export type SuperAdminGroupAction =
  | "suspend"
  | "resume"
  | "remove"
  | "add"
  | "set_role";

export interface InstanceSettings {
  company_name: string | null;
  logo: string | null;
}

/** Setup-guide steps done in the guide's organization, read from real data. */
export interface SetupGuideSteps {
  org_llm: boolean;
  mcp_server: boolean;
  automation: boolean;
  invite: boolean;
}

/** First-install state for the signed-in user. Only the first Super Admin gets real values. */
export interface SetupState {
  wizard_pending: boolean;
  guide_org_id: string | null;
  guide_dismissed: boolean;
  /** Set while the guide has an organization and is not dismissed. */
  guide_steps: SetupGuideSteps | null;
}

export interface SetupStateUpdate {
  wizard_completed?: boolean;
  guide_org_id?: string | null;
  guide_dismissed?: boolean;
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

  listUsers: async () => {
    const { data } = await openHands.get<{ users: SuperAdminApiUser[] }>(
      "/api/admin/directory/users",
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
      `/api/admin/directory/users/${userId}`,
      { status },
    );
    return data;
  },

  removeUser: async ({ userId }: { userId: string }) => {
    const { data } = await openHands.delete<{
      message: string;
      user_id: string;
    }>(`/api/admin/directory/users/${userId}`);
    return data;
  },

  updateUserGroups: async ({
    userId,
    action,
    orgIds,
    role,
  }: {
    userId: string;
    action: SuperAdminGroupAction;
    orgIds: string[];
    role?: "member" | "admin" | "owner";
  }) => {
    const { data } = await openHands.post<SuperAdminApiUser>(
      `/api/admin/directory/users/${userId}/groups`,
      { action, org_ids: orgIds, role },
    );
    return data;
  },

  getInstanceSettings: async () => {
    const { data } = await openHands.get<InstanceSettings>(
      "/api/admin/instance-settings",
    );
    return data;
  },

  updateInstanceSettings: async (settings: Partial<InstanceSettings>) => {
    const { data } = await openHands.patch<InstanceSettings>(
      "/api/admin/instance-settings",
      settings,
    );
    return data;
  },

  getSetupState: async () => {
    const { data } = await openHands.get<SetupState>("/api/admin/setup-state");
    return data;
  },

  updateSetupState: async (update: SetupStateUpdate) => {
    const { data } = await openHands.patch<SetupState>(
      "/api/admin/setup-state",
      update,
    );
    return data;
  },
};
