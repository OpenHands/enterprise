import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { scimService } from "#/api/organization-service/scim.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import {
  SCIM_CREDENTIALS_QUERY_KEY,
  SCIM_SETUP_QUERY_KEY,
} from "#/hooks/query/use-scim";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

/**
 * Shared plumbing of the SCIM credential mutations: needs the selected org,
 * toasts a failure locally (`disableToast`) and refreshes the list and the
 * setup, whose providers show what is bound.
 */
function useScimCredentialMutation<TVariables, TResult>(
  mutate: (orgId: string, variables: TVariables) => Promise<TResult>,
  successMessage?: I18nKey,
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
      if (successMessage) displaySuccessToast(t(successMessage));
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) || t(I18nKey.ERROR$GENERIC),
      );
    },
    onSettled: () =>
      Promise.all(
        [SCIM_CREDENTIALS_QUERY_KEY, SCIM_SETUP_QUERY_KEY].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key, organizationId] }),
        ),
      ),
    meta: { disableToast: true },
  });
}

/** Creates a credential. The result carries the token, available only here. */
export const useCreateScimCredential = () =>
  useScimCredentialMutation(
    (
      orgId,
      { name, authProviderId }: { name: string; authProviderId: number },
    ) => scimService.createCredential(orgId, name, authProviderId),
  );

export const useRevokeScimCredential = () =>
  useScimCredentialMutation(
    (orgId, { id }: { id: string }) => scimService.revokeCredential(orgId, id),
    I18nKey.SCIM$REVOKED,
  );
