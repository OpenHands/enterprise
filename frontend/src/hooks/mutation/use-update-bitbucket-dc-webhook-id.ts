import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { AxiosError } from "axios";
import { useTranslation } from "react-i18next";
import { integrationService } from "#/api/integration-service/integration-service.api";
import type {
  BitbucketDCResourceIdentifier,
  BitbucketDCWebhookIdUpdateResult,
} from "#/api/integration-service/integration-service.types";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

export function useUpdateBitbucketDCWebhookId() {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation<
    BitbucketDCWebhookIdUpdateResult,
    Error,
    { resource: BitbucketDCResourceIdentifier; webhookId: string },
    unknown
  >({
    mutationFn: ({ resource, webhookId }) =>
      integrationService.updateBitbucketDCWebhookId({ resource, webhookId }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["bitbucket-dc-resources"] });

      if (data.success) {
        displaySuccessToast(
          t(I18nKey.BITBUCKET_DATA_CENTER$WEBHOOK_ID_SAVE_SUCCESS),
        );
      } else if (data.error) {
        displayErrorToast(data.error);
      } else {
        displayErrorToast(
          t(I18nKey.BITBUCKET_DATA_CENTER$WEBHOOK_ID_SAVE_FAILED),
        );
      }
    },
    meta: { disableToast: true },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error as AxiosError) ||
          t(I18nKey.BITBUCKET_DATA_CENTER$WEBHOOK_ID_SAVE_FAILED),
      );
    },
  });
}
