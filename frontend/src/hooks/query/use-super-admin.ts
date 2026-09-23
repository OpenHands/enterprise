import { useQuery } from "@tanstack/react-query";
import { superAdminService } from "#/api/super-admin-service/super-admin-service.api";

export const SUPER_ADMIN_QUERY_KEYS = {
  admins: ["super-admin", "admins"] as const,
  organizations: ["super-admin", "organizations"] as const,
  users: ["super-admin", "users"] as const,
};

export const useSuperAdmins = () =>
  useQuery({
    queryKey: SUPER_ADMIN_QUERY_KEYS.admins,
    queryFn: () => superAdminService.listSuperAdmins(),
  });

export const useSuperAdminOrganizations = () =>
  useQuery({
    queryKey: SUPER_ADMIN_QUERY_KEYS.organizations,
    queryFn: () => superAdminService.listOrganizations(),
  });

export const useSuperAdminUsers = () =>
  useQuery({
    queryKey: SUPER_ADMIN_QUERY_KEYS.users,
    queryFn: () => superAdminService.listUsers(),
  });
