import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { serviceAccountsService } from "#/api/organization-service/service-accounts.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { SERVICE_ACCOUNTS_QUERY_KEY } from "#/hooks/query/use-service-accounts";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

/**
 * Shared plumbing of the service account mutations: needs the selected org,
 * toasts the result and refreshes the list. The error toast is local, so the
 * global one is off (`disableToast`).
 */
function useServiceAccountMutation<TVariables>(
  mutate: (orgId: string, variables: TVariables) => Promise<unknown>,
  successMessage: I18nKey,
) {
  const queryClient = useQueryClient();
  const { organizationId } = useSelectedOrganizationId();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (variables: TVariables) => {
      if (!organizationId) {
        throw new Error("Organization ID is required");
      }
      return mutate(organizationId, variables);
    },
    onSuccess: () => displaySuccessToast(t(successMessage)),
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) || t(I18nKey.ERROR$GENERIC),
      );
    },
    // A failed request can still have changed the account (enable succeeds,
    // then the key rotation fails with 503), so refresh on failure too.
    onSettled: () =>
      queryClient.invalidateQueries({
        queryKey: [SERVICE_ACCOUNTS_QUERY_KEY, organizationId],
      }),
    meta: { disableToast: true },
  });
}

export const useCreateServiceAccount = () =>
  useServiceAccountMutation(
    (orgId, { name }: { name: string }) =>
      serviceAccountsService.create(orgId, name),
    I18nKey.SERVICE_ACCOUNTS$CREATED,
  );

export const useRenameServiceAccount = () =>
  useServiceAccountMutation(
    (orgId, { id, name }: { id: string; name: string }) =>
      serviceAccountsService.rename(orgId, id, name),
    I18nKey.SERVICE_ACCOUNTS$RENAMED,
  );

export const useDisableServiceAccount = () =>
  useServiceAccountMutation(
    (orgId, { id }: { id: string }) =>
      serviceAccountsService.disable(orgId, id),
    I18nKey.SERVICE_ACCOUNTS$DISABLED_TOAST,
  );

export const useEnableServiceAccount = () =>
  useServiceAccountMutation(
    (orgId, { id }: { id: string }) => serviceAccountsService.enable(orgId, id),
    I18nKey.SERVICE_ACCOUNTS$ENABLED_TOAST,
  );

export const useDeleteServiceAccount = () =>
  useServiceAccountMutation(
    (orgId, { id }: { id: string }) => serviceAccountsService.delete(orgId, id),
    I18nKey.SERVICE_ACCOUNTS$DELETED,
  );
