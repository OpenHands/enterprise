import { queryOptions, useQuery } from "@tanstack/react-query";
import axios, { AxiosError } from "axios";
import AuthService from "#/api/auth-service/auth-service.api";
import { WebClientConfig } from "#/api/option-service/option.types";
import { useConfig } from "./use-config";
import { useIsOnIntermediatePage } from "#/hooks/use-is-on-intermediate-page";

export const getIsAuthedQueryOptions = (appMode: WebClientConfig["app_mode"]) =>
  queryOptions({
    queryKey: ["user", "authenticated", appMode],
    queryFn: async () => {
      try {
        await AuthService.authenticate(appMode);
        return true;
      } catch (error) {
        if (axios.isAxiosError(error)) {
          const axiosError = error as AxiosError;
          if (axiosError.response?.status === 401) {
            return false;
          }
        }
        throw error;
      }
    },
    staleTime: 1000 * 60 * 5,
    gcTime: 1000 * 60 * 15,
    retry: false,
    meta: {
      disableToast: true,
    },
  });

export const useIsAuthed = () => {
  const { data: config } = useConfig();
  const isOnIntermediatePage = useIsOnIntermediatePage();
  const appMode = config?.app_mode;

  return useQuery({
    ...getIsAuthedQueryOptions(appMode ?? "oss"),
    enabled: !!appMode && !isOnIntermediatePage,
  });
};
