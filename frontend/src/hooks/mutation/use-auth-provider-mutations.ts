import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { authProvidersService } from "#/api/auth-providers-service/auth-providers-service.api";
import type {
  CreateAuthProviderParams,
  UpdateAuthProviderParams,
} from "#/api/auth-providers-service/auth-providers.types";
import { AUTH_PROVIDERS_QUERY_KEY } from "#/hooks/query/use-auth-providers";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

export const useCreateAuthProvider = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (params: CreateAuthProviderParams) =>
      authProvidersService.createAuthProvider(params),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_CREATE_SUCCESS));
      queryClient.invalidateQueries({ queryKey: AUTH_PROVIDERS_QUERY_KEY });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_CREATE_ERROR),
      );
    },
  });
};

export const useUpdateAuthProvider = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({
      id,
      ...params
    }: { id: number } & UpdateAuthProviderParams) =>
      authProvidersService.updateAuthProvider({ id, ...params }),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_UPDATE_SUCCESS));
      queryClient.invalidateQueries({ queryKey: AUTH_PROVIDERS_QUERY_KEY });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_UPDATE_ERROR),
      );
    },
  });
};

/** Backs the SAML form's "Metadata URL" field -- fetches/parses the IdP's
 * metadata document (``authProvidersService.previewSamlMetadata``) so the
 * caller can prefill the rest of the form from it. A failed/invalid URL is
 * an expected, recoverable state here (the admin may still be typing, or
 * their IdP may not have the metadata endpoint live yet), not shown as a
 * toast -- callers handle success/error inline via ``mutate(url, { onSuccess,
 * onError })``. */
export const usePreviewSamlMetadata = () =>
  useMutation({
    meta: { disableToast: true },
    mutationFn: (metadataUrl: string) =>
      authProvidersService.previewSamlMetadata(metadataUrl),
  });

export const useDeleteAuthProvider = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ id }: { id: number }) =>
      authProvidersService.deleteAuthProvider({ id }),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_DELETE_SUCCESS));
      queryClient.invalidateQueries({ queryKey: AUTH_PROVIDERS_QUERY_KEY });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_DELETE_ERROR),
      );
    },
  });
};

/** Re-points a "User Link" (``auth_provider_users`` row) to a different,
 * already-existing user -- see
 * ``server.routes.oauth_providers_admin.update_auth_provider_user``. */
export const useUpdateAuthProviderUserLink = (providerId: number) => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ linkId, userId }: { linkId: number; userId: string }) =>
      authProvidersService.updateAuthProviderUser({
        providerId,
        linkId,
        userId,
      }),
    onSuccess: () => {
      displaySuccessToast(
        t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINK_UPDATE_SUCCESS),
      );
      queryClient.invalidateQueries({
        queryKey: [...AUTH_PROVIDERS_QUERY_KEY, providerId, "users"],
      });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINK_UPDATE_ERROR),
      );
    },
  });
};
