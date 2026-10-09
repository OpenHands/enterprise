import { openHands } from "../open-hands-axios";
import { AdminUserPage } from "./admin.types";

/**
 * Admin Service API - instance-wide, super-admin-only endpoints that are
 * not scoped to any single organization (``/api/admin/*``, distinct from
 * ``/api/admin/super-admins`` wrapped by ``superAdminService``).
 */
export const adminService = {
  /** Whether the current user holds the instance-level super-admin role. */
  getMySuperAdminStatus: async (): Promise<boolean> => {
    const { data } = await openHands.get<{ is_super_admin: boolean }>(
      "/api/admin/super-admins/me",
    );
    return data.is_super_admin;
  },

  /**
   * List every user on the instance, regardless of organization. Powers
   * the org-members page's "All Users" view, shown to a super admin when
   * their Personal Workspace is selected.
   */
  getAllUsers: async ({
    page = 1,
    limit = 10,
    email,
  }: {
    page?: number;
    limit?: number;
    email?: string;
  }): Promise<AdminUserPage> => {
    const params = new URLSearchParams();
    const offset = (page - 1) * limit;
    params.set("page_id", String(offset));
    params.set("limit", String(limit));
    if (email) {
      params.set("email", email);
    }

    const { data } = await openHands.get<AdminUserPage>(
      `/api/admin/users?${params.toString()}`,
    );
    return data;
  },

  getAllUsersCount: async ({ email }: { email?: string }): Promise<number> => {
    const params = new URLSearchParams();
    if (email) {
      params.set("email", email);
    }

    const { data } = await openHands.get<number>(
      `/api/admin/users/count?${params.toString()}`,
    );
    return data;
  },

  /**
   * Permanently delete a user account -- in every organization it belongs
   * to, not just one -- via the instance-wide "Remove" action in the "All
   * Users" view.
   */
  deleteUser: async (userId: string): Promise<void> => {
    await openHands.delete(`/api/admin/users/${userId}`);
  },
};
