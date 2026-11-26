import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { warmRuntimesService } from "#/api/warm-runtimes-service/warm-runtimes-service.api";
import type {
  CreateWarmRuntimeParams,
  UpdateWarmRuntimeAccessParams,
  WarmRuntimeFields,
} from "#/api/warm-runtimes-service/warm-runtimes.types";
import { WARM_RUNTIMES_QUERY_KEY } from "#/hooks/query/use-warm-runtimes";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

export const useCreateWarmRuntime = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (params: CreateWarmRuntimeParams) =>
      warmRuntimesService.createWarmRuntime(params),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_CREATE_SUCCESS));
      queryClient.invalidateQueries({ queryKey: WARM_RUNTIMES_QUERY_KEY });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_CREATE_ERROR),
      );
    },
  });
};

export const useUpdateWarmRuntime = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (params: WarmRuntimeFields & { name: string }) =>
      warmRuntimesService.updateWarmRuntime(params),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_UPDATE_SUCCESS));
      queryClient.invalidateQueries({ queryKey: WARM_RUNTIMES_QUERY_KEY });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_UPDATE_ERROR),
      );
    },
  });
};

export const useDeleteWarmRuntime = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: ({ name }: { name: string }) =>
      warmRuntimesService.deleteWarmRuntime({ name }),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_DELETE_SUCCESS));
      queryClient.invalidateQueries({ queryKey: WARM_RUNTIMES_QUERY_KEY });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_DELETE_ERROR),
      );
    },
  });
};

export const useUpdateWarmRuntimeAccess = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (params: UpdateWarmRuntimeAccessParams) =>
      warmRuntimesService.updateWarmRuntimeAccess(params),
    onSuccess: () => {
      displaySuccessToast(
        t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_PERMISSIONS_SUCCESS),
      );
      queryClient.invalidateQueries({ queryKey: WARM_RUNTIMES_QUERY_KEY });
    },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) ||
          t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_PERMISSIONS_ERROR),
      );
    },
  });
};
