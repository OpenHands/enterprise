import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { idpService } from "#/api/idp-service/idp-service.api";
import { CreatePasswordUserParams } from "#/api/idp-service/idp.types";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

export const useCreatePasswordUser = () => {
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (params: CreatePasswordUserParams) =>
      idpService.createPasswordUser(params),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$PASSWORD_USER_CREATE_SUCCESS));
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$PASSWORD_USER_CREATE_ERROR),
      );
    },
  });
};
