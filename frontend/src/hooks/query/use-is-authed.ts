import type { UseQueryResult } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import axios from "axios";
import AuthService from "#/api/auth-service/auth-service.api";
import { useConfig } from "./use-config";
import { useIsOnIntermediatePage } from "#/hooks/use-is-on-intermediate-page";

type AuthenticationResult =
  | boolean
  | { authenticated: boolean; acceptedTos: boolean };
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
  const authMode = config?.auth_mode;

  const query = useQuery({
    queryKey: [
      "user",
      "authenticated",
      appMode,
      ...(authMode === "native" ? [authMode] : []),
    ],
    queryFn: async () => {
      try {
        if (authMode === "native") {
          const session = await AuthService.nativeSession();
          return { authenticated: true, acceptedTos: session.accepted_tos };
        }
        // If in OSS mode or authentication succeeds, return true
        if (!appMode) throw new Error("Application configuration is required.");
        await AuthService.authenticate(appMode);
        return true;
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
    enabled: !!appMode && (!isOnIntermediatePage || authMode === "native"),
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
