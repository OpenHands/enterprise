import { useMutation, useQueryClient } from "@tanstack/react-query";
import { isAxiosError, type AxiosError } from "axios";
import { useTranslation } from "react-i18next";
import { integrationService } from "#/api/integration-service/integration-service.api";
import type { AzureDevOpsWebhookInstallationResult } from "#/api/integration-service/integration-service.types";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

export function useReinstallAzureDevOpsWebhook(organization?: string) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation<
    AzureDevOpsWebhookInstallationResult,
    AxiosError,
    void,
    unknown
  >({
    mutationFn: () =>
      integrationService.reinstallAzureDevOpsWebhook(organization),
    onSuccess: async (data) => {
      const queryKey = ["azure-devops-resources", organization];
      if (!data.success) {
        void queryClient.invalidateQueries({ queryKey });
        displayErrorToast(
          data.error || t(I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALL_FAILED),
        );
        return;
      }

      await queryClient.cancelQueries({ queryKey });
      await queryClient.invalidateQueries({ queryKey, refetchType: "none" });
      try {
        const status = await queryClient.fetchQuery({
          queryKey,
          queryFn: () =>
            integrationService.getAzureDevOpsResources(organization),
          staleTime: 0,
          retry: false,
        });
        if (
          status.can_view_webhooks &&
          status.webhook_installed &&
          (!status.automation_events_enabled ||
            status.automation_webhooks_installed)
        ) {
          displaySuccessToast(t(I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALL_SUCCESS));
        } else {
          displayErrorToast(t(I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALL_UNVERIFIED));
        }
      } catch (error) {
        const detail = isAxiosError(error)
          ? retrieveAxiosErrorMessage(error)
          : null;
        displayErrorToast(
          [t(I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALL_UNVERIFIED), detail]
            .filter(Boolean)
            .join(" "),
        );
      }
    },
    onError: (error) => {
      void queryClient.invalidateQueries({
        queryKey: ["azure-devops-resources", organization],
      });
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALL_FAILED),
      );
    },
  });
}
