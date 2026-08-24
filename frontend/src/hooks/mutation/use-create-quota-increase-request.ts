import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { useTranslation } from "react-i18next";
import {
  quotaService,
  type CreateQuotaIncreaseRequest,
} from "#/api/quota-service/quota-service.api";
import { QUOTA_QUERY_KEYS } from "#/hooks/query/use-quota-status";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

export const useCreateQuotaIncreaseRequest = () => {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (body: CreateQuotaIncreaseRequest) =>
      quotaService.createIncreaseRequest(body),
    onSuccess: () => {
      displaySuccessToast(t("SETTINGS$QUOTA_REQUEST_SENT"));
      queryClient.invalidateQueries({ queryKey: QUOTA_QUERY_KEYS.status });
    },
    onError: (error: AxiosError) => {
      const message = retrieveAxiosErrorMessage(error);
      displayErrorToast(message || t("SETTINGS$QUOTA_REQUEST_FAILED"));
    },
  });
};
