import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { AxiosError } from "axios";
import { integrationService } from "#/api/integration-service/integration-service.api";
import type { AzureDevOpsDeliveryTest } from "#/api/integration-service/integration-service.types";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

export function useTestAzureDevOpsWebhookDelivery(organization: string) {
  const client = useQueryClient();
  const { t } = useTranslation();
  return useMutation<AzureDevOpsDeliveryTest, AxiosError, void>({
    mutationFn: () =>
      integrationService.testAzureDevOpsWebhookDelivery(organization),
    onSuccess: (result) => {
      if (result.outcome === "verified") {
        displaySuccessToast(t(I18nKey.AZURE_DEVOPS$DELIVERY_VERIFIED));
      } else {
        displayErrorToast(
          result.error || t(I18nKey.AZURE_DEVOPS$DELIVERY_FAILED),
        );
      }
    },
    onError: (error) =>
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.AZURE_DEVOPS$DELIVERY_FAILED),
      ),
    onSettled: () =>
      client.invalidateQueries({
        queryKey: ["azure-devops-resources", organization],
      }),
  });
}
