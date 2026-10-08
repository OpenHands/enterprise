import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  superAdminService,
  type InstanceSettings,
  type ProvisionUserRequest,
  type ProvisionUserResponse,
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

export const useGrantSuperAdmin = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ email }: { email: string }) =>
      superAdminService.grantSuperAdmin({ email }),
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

export const useUpdateSuperAdminOrganizationStatus = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({
      orgId,
      status,
    }: {
      orgId: string;
      status: "active" | "suspended";
    }) => superAdminService.updateOrganizationStatus({ orgId, status }),
    onSuccess: (_data, variables) => {
      displaySuccessToast(
        variables.status === "suspended"
          ? t(I18nKey.SUPER_ADMIN$ORG_SUSPEND_SUCCESS)
          : t(I18nKey.SUPER_ADMIN$ORG_RESUME_SUCCESS),
      );
      queryClient.invalidateQueries({
        queryKey: SUPER_ADMIN_QUERY_KEYS.organizations,
      });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$ORG_STATUS_ERROR),
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

export const useProvisionSuperAdminUser = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({
      orgId,
      payload,
    }: {
      orgId: string;
      payload: ProvisionUserRequest;
    }) => superAdminService.provisionUser({ orgId, payload }),
    onSuccess: (data) => {
      displaySuccessToast(
        data.created
          ? t(I18nKey.SUPER_ADMIN$PROVISION_USER_SUCCESS)
          : t(I18nKey.SUPER_ADMIN$PROVISION_USER_REPROVISIONED),
      );
      queryClient.invalidateQueries({ queryKey: SUPER_ADMIN_QUERY_KEYS.users });
      queryClient.invalidateQueries({
        queryKey: SUPER_ADMIN_QUERY_KEYS.organizations,
      });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$PROVISION_USER_ERROR),
      );
    },
  });
};

export const useProvisionUserToGroups = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({
      assignments,
      email,
      password,
    }: {
      assignments: { orgId: string; role: "member" | "admin" | "owner" }[];
      email: string;
      password?: string;
    }) =>
      // Provision one organization at a time. The first call creates the
      // account; later calls attach that same user to the other groups.
      assignments.reduce<Promise<ProvisionUserResponse[]>>(
        (chain, assignment) =>
          chain.then(async (results) => {
            const next = await superAdminService.provisionUser({
              orgId: assignment.orgId,
              payload: {
                email,
                role: assignment.role,
                ...(password ? { password } : {}),
              },
            });
            return [...results, next];
          }),
        Promise.resolve([]),
      ),
    onSuccess: (results) => {
      displaySuccessToast(
        results.some((result) => result.created)
          ? t(I18nKey.SUPER_ADMIN$PROVISION_USER_SUCCESS)
          : t(I18nKey.SUPER_ADMIN$PROVISION_USER_REPROVISIONED),
      );
      queryClient.invalidateQueries({ queryKey: SUPER_ADMIN_QUERY_KEYS.users });
      queryClient.invalidateQueries({
        queryKey: SUPER_ADMIN_QUERY_KEYS.organizations,
      });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$PROVISION_USER_ERROR),
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
