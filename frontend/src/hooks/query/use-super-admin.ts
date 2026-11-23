import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  SUPER_ADMIN_MAX_PAGE_SIZE,
  superAdminService,
  type SuperAdminApiOrg,
} from "#/api/super-admin-service/super-admin-service.api";
import { useConfig } from "#/hooks/query/use-config";
import { CONFIG_CACHE_OPTIONS } from "#/hooks/query/query-keys";

export const SUPER_ADMIN_QUERY_KEYS = {
  admins: ["super-admin", "admins"] as const,
  organizations: ["super-admin", "organizations"] as const,
  users: ["super-admin", "users"] as const,
  instanceSettings: ["super-admin", "instance-settings"] as const,
  setupState: ["super-admin", "setup-state"] as const,
};

export const useSuperAdmins = (
  params: Parameters<typeof superAdminService.listSuperAdmins>[0] = {},
) =>
  useQuery({
    queryKey: [...SUPER_ADMIN_QUERY_KEYS.admins, params] as const,
    queryFn: () => superAdminService.listSuperAdmins(params),
    placeholderData: keepPreviousData,
  });

export const useSuperAdminOrganizations = (
  params: Parameters<typeof superAdminService.listOrganizations>[0] = {},
) =>
  useQuery({
    queryKey: [
      ...SUPER_ADMIN_QUERY_KEYS.organizations,
      "page",
      params,
    ] as const,
    queryFn: () => superAdminService.listOrganizations(params),
    placeholderData: keepPreviousData,
  });

/**
 * Every team organization (personal workspaces left out), fetched one full
 * page at a time. For pickers that list organizations rather than users, so
 * the set stays small next to the number of accounts.
 */
export const useSuperAdminTeamOrganizations = (enabled = true) =>
  useQuery({
    queryKey: [...SUPER_ADMIN_QUERY_KEYS.organizations, "team"] as const,
    queryFn: async () => {
      const organizations: SuperAdminApiOrg[] = [];
      for (let page = 1; ; page += 1) {
        // Pages are fetched in order, as each one tells whether another follows.
        // eslint-disable-next-line no-await-in-loop
        const result = await superAdminService.listOrganizations({
          page,
          limit: SUPER_ADMIN_MAX_PAGE_SIZE,
          includePersonal: false,
        });
        organizations.push(...result.items);
        if (organizations.length >= result.total || result.items.length === 0) {
          return organizations;
        }
      }
    },
    enabled,
  });

export const useSuperAdminUsers = (
  params: Parameters<typeof superAdminService.listUsers>[0] = {},
  enabled = true,
) =>
  useQuery({
    queryKey: [...SUPER_ADMIN_QUERY_KEYS.users, "page", params] as const,
    queryFn: () => superAdminService.listUsers(params),
    placeholderData: keepPreviousData,
    enabled,
  });

export const useSuperAdminUser = (userId: string | null | undefined) =>
  useQuery({
    queryKey: [...SUPER_ADMIN_QUERY_KEYS.users, "detail", userId] as const,
    queryFn: () => superAdminService.getUser({ userId: userId as string }),
    enabled: !!userId,
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
