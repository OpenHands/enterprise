import { openHands } from "../open-hands-axios";
import {
  AdminOrgListResponse,
  SuperAdminStatusResponse,
} from "./admin-service.types";

/**
 * Client for the self-hosted super-admin dashboard API (`/api/admin/*`).
 *
 * These routes only exist on self-hosted enterprise installs where the
 * dashboard is enabled; on other deployments the endpoints are not mounted
 * and requests 404. Callers gate on {@link getSuperAdminStatus} first.
 */
export const adminService = {
  /**
   * Whether the current user may use the super-admin dashboard. Always
   * resolves for an authenticated user (200), so it is safe to call without
   * first knowing the user's role.
   */
  getSuperAdminStatus: async (): Promise<SuperAdminStatusResponse> => {
    const { data } = await openHands.get<SuperAdminStatusResponse>(
      "/api/admin/super-admin-status",
    );
    return data;
  },

  /** List every team organization in the instance. Requires super-admin. */
  listAllOrgs: async (): Promise<AdminOrgListResponse> => {
    const { data } =
      await openHands.get<AdminOrgListResponse>("/api/admin/orgs");
    return data;
  },
};
