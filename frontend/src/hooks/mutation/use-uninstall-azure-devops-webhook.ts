import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { AxiosError } from "axios";
import { useTranslation } from "react-i18next";
import { integrationService } from "#/api/integration-service/integration-service.api";
import type { AzureDevOpsWebhookInstallationResult } from "#/api/integration-service/integration-service.types";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

export function useUninstallAzureDevOpsWebhook(organization?: string) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation<
    AzureDevOpsWebhookInstallationResult,
    AxiosError,
    void,
    unknown
  >({
    mutationFn: () =>
      integrationService.uninstallAzureDevOpsWebhook(organization),
    onSuccess: (data) => {
      queryClient.invalidateQueries({
        queryKey: ["azure-devops-resources", organization],
      });

      if (data.success) {
        displaySuccessToast(t(I18nKey.AZURE_DEVOPS$WEBHOOK_UNINSTALL_SUCCESS));
      } else if (data.error) {
        displayErrorToast(data.error);
      } else {
        displayErrorToast(t(I18nKey.AZURE_DEVOPS$WEBHOOK_UNINSTALL_FAILED));
      }
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.AZURE_DEVOPS$WEBHOOK_UNINSTALL_FAILED),
      );
    },
  });
}
