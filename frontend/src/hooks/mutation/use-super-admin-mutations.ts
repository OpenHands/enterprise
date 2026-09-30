import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  superAdminService,
  type ProvisionUserRequest,
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
