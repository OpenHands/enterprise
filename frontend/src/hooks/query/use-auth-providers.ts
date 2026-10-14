import { useQuery } from "@tanstack/react-query";
import { authProvidersService } from "#/api/auth-providers-service/auth-providers-service.api";

export const AUTH_PROVIDERS_QUERY_KEY = ["super-admin", "auth-providers"];

export const useAuthProviders = () =>
  useQuery({
    queryKey: AUTH_PROVIDERS_QUERY_KEY,
    queryFn: () => authProvidersService.listAuthProviders(),
  });
