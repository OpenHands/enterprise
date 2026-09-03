import { useQuery } from "@tanstack/react-query";
import { adminService } from "#/api/admin-service/admin-service.api";

/**
 * List every team organization in the instance (super-admin dashboard).
 *
 * Only enable this once {@link useSuperAdminStatus} confirms the user is a
 * super admin, since the endpoint is permission-gated and 403s otherwise.
 */
export const useAllOrganizations = (enabled: boolean) =>
  useQuery({
    queryKey: ["admin", "all-organizations"],
    queryFn: adminService.listAllOrgs,
    enabled,
    staleTime: 1000 * 60, // 1 minute
  });
