import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

/**
 * Sets or removes the monthly budget of a service account through the
 * budget override API, which the Budgets page uses for members.
 * `monthlyLimit: null` removes the override.
 */
export const useSetServiceAccountBudget = (saId: string) => {
  const queryClient = useQueryClient();
  const { organizationId } = useSelectedOrganizationId();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: async ({ monthlyLimit }: { monthlyLimit: number | null }) => {
      if (!organizationId) {
        throw new Error("Organization ID is required");
      }
      if (monthlyLimit === null) {
        await organizationService.deleteBudgetOverride({
          orgId: organizationId,
          userId: saId,
        });
        return;
      }
      await organizationService.upsertBudgetOverride({
        orgId: organizationId,
        userId: saId,
        payload: { monthly_limit: monthlyLimit, is_disabled: false },
      });
    },
    onSuccess: () => displaySuccessToast(t(I18nKey.SETTINGS$SAVED)),
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) || t(I18nKey.ERROR$GENERIC),
      );
    },
    onSettled: () =>
      queryClient.invalidateQueries({
        queryKey: ["organizations", "budgets", organizationId],
      }),
  });
};
