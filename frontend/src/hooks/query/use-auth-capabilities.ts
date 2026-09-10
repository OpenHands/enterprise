import { useQuery } from "@tanstack/react-query";
import AuthService from "#/api/auth-service/auth-service.api";
import { useConfig } from "./use-config";

export function useAuthCapabilities() {
  const config = useConfig({ enabled: true });
  const query = useQuery({
    queryKey: ["auth", "capabilities", config.data?.app_mode],
    queryFn: AuthService.getCapabilities,
    enabled: config.data?.app_mode === "saas",
    staleTime: Infinity,
    retry: false,
    meta: { disableToast: true },
  });
  return {
    data: query.data,
    error: query.error,
    isLoading: config.isLoading || query.isLoading,
    isError: config.isError || query.isError,
    refetch: query.refetch,
  };
}
