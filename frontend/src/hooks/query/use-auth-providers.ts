import { useQuery } from "@tanstack/react-query";
import { authProvidersService } from "#/api/auth-providers-service/auth-providers-service.api";

export const AUTH_PROVIDERS_QUERY_KEY = ["super-admin", "auth-providers"];

export const useAuthProviders = () =>
  useQuery({
    queryKey: AUTH_PROVIDERS_QUERY_KEY,
    queryFn: () => authProvidersService.listAuthProviders(),
  });

export const PUBLIC_IDP_PROVIDERS_QUERY_KEY = ["public", "idp-providers"];

/** Public, unauthenticated list of configured login IDPs -- backs the
 * "Let's Get Started" login screen's provider buttons when
 * ``ENABLE_OAUTH_V2_LOGIN`` is on. Unlike ``useAuthProviders``, this never
 * requires a session and is safe to call from the unauthenticated login
 * route. */
export const usePublicIdpProviders = (enabled: boolean) =>
  useQuery({
    queryKey: PUBLIC_IDP_PROVIDERS_QUERY_KEY,
    queryFn: () => authProvidersService.listPublicIdpProviders(),
    enabled,
  });
