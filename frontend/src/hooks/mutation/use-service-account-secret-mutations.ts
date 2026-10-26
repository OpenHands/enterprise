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

/** Shared plumbing of the secret mutations of one service account. */
function useSecretMutation<TVariables>(
  saId: string,
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
    onSuccess: () => {
      displaySuccessToast(t(successMessage));
      queryClient.invalidateQueries({
        queryKey: [SERVICE_ACCOUNTS_QUERY_KEY, organizationId, saId, "secrets"],
      });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) || t(I18nKey.ERROR$GENERIC),
      );
    },
    meta: { disableToast: true },
  });
}

/** Creates a secret, or replaces its value and description. */
export const usePutServiceAccountSecret = (saId: string) =>
  useSecretMutation(
    saId,
    (
      orgId,
      {
        name,
        value,
        description,
      }: { name: string; value: string; description?: string },
    ) =>
      serviceAccountsService.putSecret(orgId, saId, name, {
        value,
        description,
      }),
    I18nKey.SERVICE_ACCOUNTS$SECRET_SAVED,
  );

export const useDeleteServiceAccountSecret = (saId: string) =>
  useSecretMutation(
    saId,
    (orgId, { name }: { name: string }) =>
      serviceAccountsService.deleteSecret(orgId, saId, name),
    I18nKey.SERVICE_ACCOUNTS$SECRET_DELETED,
  );
