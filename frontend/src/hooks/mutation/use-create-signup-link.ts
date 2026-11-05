import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { idpService } from "#/api/idp-service/idp-service.api";
import { CreateSignupLinkParams } from "#/api/idp-service/idp.types";
import { I18nKey } from "#/i18n/declaration";
import { displayErrorToast } from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

/**
 * Mint a super-admin-only sign-up link. No query invalidation on success:
 * the link itself isn't persisted anywhere the UI lists (unlike pending
 * invitations), it's only displayed once for the caller to copy and share.
 *
 * ``errorMessageKey`` lets callers that mint the same link for a different
 * purpose -- e.g. ``CreatePasswordResetLinkModal``, which mints one for an
 * *existing* user rather than inviting someone new -- show a fallback error
 * that matches what the caller actually asked for.
 */
export const useCreateSignupLink = (
  errorMessageKey: I18nKey = I18nKey.ORG$CREATE_SIGNUP_LINK_ERROR,
) => {
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (params: CreateSignupLinkParams) =>
      idpService.createSignupLink(params),
    onError: (error) => {
      const errorMessage = retrieveAxiosErrorMessage(error);
      displayErrorToast(errorMessage || t(errorMessageKey));
    },
  });
};
