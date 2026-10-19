import { useQuery } from "@tanstack/react-query";
import { superAdminService } from "#/api/super-admin-service/super-admin-service.api";
import { useConfig } from "#/hooks/query/use-config";
import { CONFIG_CACHE_OPTIONS } from "#/hooks/query/query-keys";

export const SUPER_ADMIN_QUERY_KEYS = {
  admins: ["super-admin", "admins"] as const,
  organizations: ["super-admin", "organizations"] as const,
  users: ["super-admin", "users"] as const,
  instanceSettings: ["super-admin", "instance-settings"] as const,
  setupState: ["super-admin", "setup-state"] as const,
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

/** Company name and logo for the instance. Readable by any signed-in user. */
export const useInstanceSettings = () => {
  const { data: config } = useConfig();

  return useQuery({
    queryKey: SUPER_ADMIN_QUERY_KEYS.instanceSettings,
    queryFn: () => superAdminService.getInstanceSettings(),
    enabled: config?.feature_flags?.enable_super_admin === true,
    ...CONFIG_CACHE_OPTIONS,
    meta: { disableToast: true },
  });
};

/** First-install wizard and setup-guide state for the signed-in user. */
export const useSetupState = () => {
  const { data: config } = useConfig();

  return useQuery({
    queryKey: SUPER_ADMIN_QUERY_KEYS.setupState,
    queryFn: () => superAdminService.getSetupState(),
    enabled: config?.feature_flags?.enable_super_admin === true,
    ...CONFIG_CACHE_OPTIONS,
    meta: { disableToast: true },
  });
};
