import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { adminService } from "#/api/admin-service/admin-service.api";

interface UseAllUsersParams {
  page?: number;
  limit?: number;
  email?: string;
  /** Only super admins can call this endpoint; keep it disabled otherwise. */
  enabled?: boolean;
}

/**
 * Instance-wide user directory, paginated. Counterpart to
 * ``useOrganizationMembers`` for the org-members page's "All Users" admin
 * view, shown to super admins when their Personal Workspace is selected
 * (super-admin only, see ``GET /api/admin/users``).
 */
export const useAllUsers = ({
  page = 1,
  limit = 10,
  email,
  enabled = true,
}: UseAllUsersParams = {}) =>
  useQuery({
    queryKey: ["admin", "users", page, limit, email],
    queryFn: () => adminService.getAllUsers({ page, limit, email }),
    enabled,
    placeholderData: keepPreviousData,
  });
