import { useQuery } from "@tanstack/react-query";
import { adminService } from "#/api/admin-service/admin-service.api";
import { useIsAuthed } from "./use-is-authed";
import { useConfig } from "./use-config";

/**
 * Whether the current user holds the instance-level super-admin role.
 *
 * Used to grant the org-members page access regardless of organization
 * selection, and to switch its "Add members" button into signup-link
 * minting (see ``server.routes.super_admins``'s ``GET
 * /api/admin/super-admins/me``).
 *
 * The endpoint is SaaS-only, so this stays disabled (and falls back to the
 * `false` default via the caller's destructuring) in OSS mode or before the
 * user is authenticated -- mirroring ``useOrganizations``.
 */
export const useIsSuperAdmin = () => {
  const { data: userIsAuthenticated } = useIsAuthed();
  const { data: config } = useConfig();
  const isOssMode = config?.app_mode === "oss";

  return useQuery({
    queryKey: ["admin", "super-admin-status"],
    queryFn: adminService.getMySuperAdminStatus,
    staleTime: 1000 * 60 * 5,
    enabled: !!userIsAuthenticated && !isOssMode,
  });
};
