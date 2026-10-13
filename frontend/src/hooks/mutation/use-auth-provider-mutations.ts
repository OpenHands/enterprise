import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { oauthProvidersService } from "#/api/oauth-providers-service/oauth-providers-service.api";
import type {
  CreateOAuthProviderParams,
  UpdateOAuthProviderParams,
} from "#/api/oauth-providers-service/oauth-providers.types";
import { OAUTH_PROVIDERS_QUERY_KEY } from "#/hooks/query/use-oauth-providers";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

export const useCreateOAuthProvider = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (params: CreateOAuthProviderParams) =>
      oauthProvidersService.createOAuthProvider(params),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_CREATE_SUCCESS));
      queryClient.invalidateQueries({ queryKey: OAUTH_PROVIDERS_QUERY_KEY });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_CREATE_ERROR),
      );
    },
  });
};

export const useUpdateOAuthProvider = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({
      id,
      ...params
    }: { id: number } & UpdateOAuthProviderParams) =>
      oauthProvidersService.updateOAuthProvider({ id, ...params }),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_UPDATE_SUCCESS));
      queryClient.invalidateQueries({ queryKey: OAUTH_PROVIDERS_QUERY_KEY });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_UPDATE_ERROR),
      );
    },
  });
};

export const useDeleteOAuthProvider = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ id }: { id: number }) =>
      oauthProvidersService.deleteOAuthProvider({ id }),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_DELETE_SUCCESS));
      queryClient.invalidateQueries({ queryKey: OAUTH_PROVIDERS_QUERY_KEY });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_DELETE_ERROR),
      );
    },
  });
};
