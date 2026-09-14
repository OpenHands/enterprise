import type { UseQueryResult } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import axios from "axios";
import AuthService from "#/api/auth-service/auth-service.api";
import { useConfig } from "./use-config";
import { useIsOnIntermediatePage } from "#/hooks/use-is-on-intermediate-page";

import { AuthenticationResult } from "#/api/auth-adapter";
import { useAuthentication } from "#/hooks/use-authentication";

export type AuthenticationQuery = Pick<
  UseQueryResult<AuthenticationResult>,
  | "isLoading"
  | "isError"
  | "isPending"
  | "isFetching"
  | "isFetched"
  | "isSuccess"
  | "status"
  | "error"
  | "fetchStatus"
  | "refetch"
> & { data: boolean | undefined; acceptedTos: boolean | undefined };

export const useIsAuthed = (): AuthenticationQuery => {
  const { data: config } = useConfig();
  const isOnIntermediatePage = useIsOnIntermediatePage();

  const appMode = config?.app_mode;
  const authentication = useAuthentication();

  const query = useQuery({
    queryKey: authentication.sessionQueryKey(appMode),
    queryFn: async () => {
      try {
        if (!appMode) throw new Error("Application configuration is required.");
        return await authentication.authenticate(AuthService, appMode);
      } catch (error) {
        // If it's a 401 error, return false (not authenticated)
        if (
          axios.isAxiosError<unknown, unknown>(error) &&
          error.response?.status === 401
        )
          return false;
        // For any other error, throw it to put the query in error state
        throw error;
      }
    },
    enabled:
      !!appMode && authentication.checkSessionOnPage(isOnIntermediatePage),
    staleTime: 1000 * 60 * 5, // 5 minutes
    gcTime: 1000 * 60 * 15, // 15 minutes
    retry: false,
    meta: {
      disableToast: true,
    },
  });
  return {
    isLoading: query.isLoading,
    isError: query.isError,
    isPending: query.isPending,
    isFetching: query.isFetching,
    isFetched: query.isFetched,
    isSuccess: query.isSuccess,
    status: query.status,
    error: query.error,
    fetchStatus: query.fetchStatus,
    refetch: query.refetch,
    data:
      typeof query.data === "object" ? query.data.authenticated : query.data,
    acceptedTos:
      typeof query.data === "object" ? query.data.acceptedTos : undefined,
  };
};
