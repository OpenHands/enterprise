import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { useTranslation } from "react-i18next";
import {
  superAdminService,
  type InstanceSettings,
  type SetupStateUpdate,
  type SuperAdminGroupAction,
} from "#/api/super-admin-service/super-admin-service.api";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { SUPER_ADMIN_QUERY_KEYS } from "#/hooks/query/use-super-admin";
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import { adminService } from "#/api/admin-service/admin-service.api";
import type { WebClientConfig } from "#/api/option-service/option.types";

export const useGrantSuperAdmin = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (params: { email: string } | { userId: string }) =>
      superAdminService.grantSuperAdmin(params),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$GRANT_ADMIN_SUCCESS));
      queryClient.invalidateQueries({
        queryKey: SUPER_ADMIN_QUERY_KEYS.admins,
      });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$GRANT_ADMIN_ERROR),
      );
    },
  });
};

export const useRevokeSuperAdmin = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ userId }: { userId: string }) =>
      superAdminService.revokeSuperAdmin({ userId }),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$REVOKE_SUCCESS));
      queryClient.invalidateQueries({
        queryKey: SUPER_ADMIN_QUERY_KEYS.admins,
      });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) || t(I18nKey.SUPER_ADMIN$REVOKE_ERROR),
      );
    },
  });
};

export const useDeleteSuperAdminOrganization = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ orgId }: { orgId: string }) =>
      superAdminService.deleteOrganization({ orgId }),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.ORG$DELETE_ORGANIZATION_SUCCESS));
      queryClient.invalidateQueries({
        queryKey: SUPER_ADMIN_QUERY_KEYS.organizations,
      });
      queryClient.invalidateQueries({ queryKey: ["organizations"] });
      queryClient.invalidateQueries({ queryKey: SUPER_ADMIN_QUERY_KEYS.users });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.ORG$DELETE_ORGANIZATION_ERROR),
      );
    },
  });
};

export const useUpdateSuperAdminUserStatus = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({
      userId,
      status,
    }: {
      userId: string;
      status: "active" | "inactive";
    }) => superAdminService.updateUserStatus({ userId, status }),
    onSuccess: (_data, variables) => {
      displaySuccessToast(
        variables.status === "inactive"
          ? t(I18nKey.SUPER_ADMIN$USER_SUSPEND_SUCCESS)
          : t(I18nKey.SUPER_ADMIN$USER_RESUME_SUCCESS),
      );
      queryClient.invalidateQueries({ queryKey: SUPER_ADMIN_QUERY_KEYS.users });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$USER_STATUS_ERROR),
      );
    },
  });
};

export const useRemoveSuperAdminUser = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ userId }: { userId: string }) =>
      superAdminService.removeUser({ userId }),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$USER_REMOVE_SUCCESS));
      queryClient.invalidateQueries({ queryKey: SUPER_ADMIN_QUERY_KEYS.users });
      queryClient.invalidateQueries({
        queryKey: SUPER_ADMIN_QUERY_KEYS.organizations,
      });
      queryClient.invalidateQueries({
        queryKey: SUPER_ADMIN_QUERY_KEYS.admins,
      });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$USER_REMOVE_ERROR),
      );
    },
  });
};

export const useUpdateSuperAdminUserGroups = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({
      userId,
      action,
      orgIds,
      role,
    }: {
      userId: string;
      action: SuperAdminGroupAction;
      orgIds: string[];
      role?: "member" | "admin" | "owner";
    }) => superAdminService.updateUserGroups({ userId, action, orgIds, role }),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$GROUPS_UPDATED));
      queryClient.invalidateQueries({ queryKey: SUPER_ADMIN_QUERY_KEYS.users });
      queryClient.invalidateQueries({
        queryKey: SUPER_ADMIN_QUERY_KEYS.organizations,
      });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$GROUPS_UPDATE_ERROR),
      );
    },
  });
};

export const useUpdateInstanceSettings = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (settings: Partial<InstanceSettings>) =>
      superAdminService.updateInstanceSettings(settings),
    onSuccess: (data) => {
      queryClient.setQueryData(SUPER_ADMIN_QUERY_KEYS.instanceSettings, data);
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$INSTANCE_LOGO_ERROR),
      );
    },
    meta: { disableToast: true },
  });
};

export const useUpdateSetupState = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (update: SetupStateUpdate) =>
      superAdminService.updateSetupState(update),
    onSuccess: (data) => {
      queryClient.setQueryData(SUPER_ADMIN_QUERY_KEYS.setupState, data);
    },
  });
};

export const SERVICE_ACCOUNTS_FLAG_KEY = "ENABLE_SERVICE_ACCOUNTS";

/**
 * Turn service accounts on or off for the whole instance by writing the
 * ``ENABLE_SERVICE_ACCOUNTS`` database flag. Before the first write the flag
 * only has its env-var default and no row, so a 404 on update creates it.
 */
export const useSetServiceAccountsEnabled = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: async (enabled: boolean) => {
      try {
        return await adminService.updateFeatureFlag(SERVICE_ACCOUNTS_FLAG_KEY, {
          enabled,
        });
      } catch (error) {
        if (axios.isAxiosError(error) && error.response?.status === 404) {
          return adminService.createFeatureFlag({
            key: SERVICE_ACCOUNTS_FLAG_KEY,
            description: "Enable Service Accounts (beta)",
            enabled,
          });
        }
        throw error;
      }
    },
    // Show the saved value at once, and keep the mutation pending (the switch
    // disabled) until the refetched config arrives.
    onSuccess: (flag) => {
      queryClient.setQueryData<WebClientConfig>(
        QUERY_KEYS.WEB_CLIENT_CONFIG,
        (config) =>
          config && {
            ...config,
            feature_flags: {
              ...config.feature_flags,
              enable_service_accounts: flag.enabled,
            },
          },
      );
      return queryClient.invalidateQueries({
        queryKey: QUERY_KEYS.WEB_CLIENT_CONFIG,
      });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) || t(I18nKey.ERROR$GENERIC),
      );
    },
    meta: { disableToast: true },
  });
};
