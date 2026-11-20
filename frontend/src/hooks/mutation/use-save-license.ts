import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  licenseService,
  type LicenseUpdate,
} from "#/api/license-service/license-service.api";
import { LICENSE_QUERY_KEYS } from "#/hooks/query/use-license-status";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

/** Saves a license in place of the one saved before. */
export const useSaveLicense = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (license: LicenseUpdate) => licenseService.saveLicense(license),
    onSuccess: (data) => {
      queryClient.setQueryData(LICENSE_QUERY_KEYS.configured, data);
      // The server checks a saved license at once.
      queryClient.invalidateQueries({ queryKey: LICENSE_QUERY_KEYS.status });
      queryClient.invalidateQueries({ queryKey: LICENSE_QUERY_KEYS.usage });
      displaySuccessToast(t(I18nKey.LICENSE$SAVED));
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) || t(I18nKey.LICENSE$SAVE_ERROR),
      );
    },
    meta: { disableToast: true },
  });
};
