import { openHands } from "../open-hands-axios";

/** One page of an instance-wide admin list; ``total`` counts every match. */
export interface SuperAdminPage<T> {
  items: T[];
  total: number;
  current_page: number;
  per_page: number;
}

export interface SuperAdminListParams {
  /** 1-based; sent to the backend as an offset ``page_id``. */
  page?: number;
  limit?: number;
  /** Case-insensitive partial match. */
  q?: string;
}

/** The largest page the backend serves. */
export const SUPER_ADMIN_MAX_PAGE_SIZE = 100;

function listQuery(
  { page = 1, limit = 20, q }: SuperAdminListParams,
  extra: Record<string, boolean | undefined> = {},
) {
  const params = new URLSearchParams();
  params.set("page_id", String((page - 1) * limit));
  params.set("limit", String(limit));
  if (q) {
    params.set("q", q);
  }
  Object.entries(extra).forEach(([key, value]) => {
    if (value !== undefined) {
      params.set(key, String(value));
    }
  });
  return params.toString();
}

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
  /** ``null`` for a pending invitation whose address has no account yet. */
  user_id: string | null;
  email: string | null;
  name: string | null;
  memberships: SuperAdminApiMembership[];
  status: "active" | "inactive";
  is_super_admin: boolean;
}

export type SuperAdminGroupAction =
  "suspend" | "resume" | "remove" | "add" | "set_role";

export interface InstanceSettings {
  company_name: string | null;
  logo: string | null;
}

/** Setup-guide steps done in the guide's organization, read from real data. */
export interface SetupGuideSteps {
  org_llm: boolean;
  git_provider: boolean;
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
  listSuperAdmins: async (params: Omit<SuperAdminListParams, "q"> = {}) => {
    const { data } = await openHands.get<SuperAdminPage<SuperAdminApiAdmin>>(
      `/api/admin/super-admins?${listQuery(params)}`,
    );
    return data;
  },

  grantSuperAdmin: async (params: { email: string } | { userId: string }) => {
    const body = "userId" in params ? { user_id: params.userId } : params;
    const { data } = await openHands.post<SuperAdminApiAdmin>(
      "/api/admin/super-admins",
      body,
    );
    return data;
  },

  revokeSuperAdmin: async ({ userId }: { userId: string }) => {
    const { data } = await openHands.delete<SuperAdminApiAdmin>(
      `/api/admin/super-admins/${userId}`,
    );
    return data;
  },

  listOrganizations: async ({
    includePersonal,
    ...params
  }: SuperAdminListParams & { includePersonal?: boolean } = {}) => {
    const { data } = await openHands.get<SuperAdminPage<SuperAdminApiOrg>>(
      `/api/admin/organizations?${listQuery(params, {
        include_personal: includePersonal,
      })}`,
    );
    return data;
  },

  deleteOrganization: async ({ orgId }: { orgId: string }) => {
    await openHands.delete(`/api/admin/organizations/${orgId}`);
  },

  listUsers: async (params: SuperAdminListParams = {}) => {
    const { data } = await openHands.get<SuperAdminPage<SuperAdminApiUser>>(
      `/api/admin/directory/users?${listQuery(params)}`,
    );
    return data;
  },

  getUser: async ({ userId }: { userId: string }) => {
    const { data } = await openHands.get<SuperAdminApiUser>(
      `/api/admin/directory/users/${userId}`,
    );
    return data;
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
