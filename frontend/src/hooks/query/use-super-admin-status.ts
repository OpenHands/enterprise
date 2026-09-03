import { useQuery } from "@tanstack/react-query";
import { adminService } from "#/api/admin-service/admin-service.api";
import { useAppMode } from "#/hooks/use-app-mode";

/**
 * Whether the current user can access the self-hosted super-admin dashboard.
 *
 * Only queried on enterprise self-hosted installs; on cloud/SaaS or OSS the
 * dashboard routes are not mounted, so we skip the request entirely and treat
 * the user as a non-super-admin. A 404 (dashboard disabled by the operator)
 * surfaces as an error and is treated as "no access" by callers.
 */
export const useSuperAdminStatus = () => {
  const { isEnterpriseSelfHosted } = useAppMode();

  return useQuery({
    queryKey: ["admin", "super-admin-status"],
    queryFn: adminService.getSuperAdminStatus,
    enabled: isEnterpriseSelfHosted,
    staleTime: 1000 * 60 * 5, // 5 minutes
    retry: false,
  });
};
