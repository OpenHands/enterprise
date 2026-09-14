import type { UseMutationResult, DefaultError } from "@tanstack/react-query";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useLiteLlmIntegration } from "#/hooks/use-litellm-integration";
import { I18nKey } from "#/i18n/declaration";
import BillingService from "#/api/billing-service/billing-service.api";
import { displayErrorToast } from "#/utils/custom-toast-handlers";

export const useCreateBillingSession = (): UseMutationResult<
  string,
  DefaultError,
  void,
  unknown
> => {
  const { requireLiteLlm } = useLiteLlmIntegration();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: () => {
      requireLiteLlm();
      return BillingService.createBillingSessionResponse();
    },
    onSuccess: (data) => {
      window.location.href = data;
    },
    onError: () => {
      displayErrorToast(t(I18nKey.BILLING$ERROR_WHILE_CREATING_SESSION));
    },
  });
};
