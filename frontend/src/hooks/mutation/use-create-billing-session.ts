import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import BillingService from "#/api/billing-service/billing-service.api";
import { displayErrorToast } from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

export const useCreateBillingSession = () => {
  const { t } = useTranslation();

  return useMutation({
    mutationFn: BillingService.createBillingSessionResponse,
    onSuccess: (data) => {
      window.location.href = data;
    },
    meta: { disableToast: true },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.BILLING$ERROR_WHILE_CREATING_SESSION),
      );
    },
  });
};
