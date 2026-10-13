import { useQuery } from "@tanstack/react-query";
import { oauthProvidersService } from "#/api/oauth-providers-service/oauth-providers-service.api";

export const OAUTH_PROVIDERS_QUERY_KEY = ["super-admin", "oauth-providers"];

export const useOAuthProviders = () =>
  useQuery({
    queryKey: OAUTH_PROVIDERS_QUERY_KEY,
    queryFn: () => oauthProvidersService.listOAuthProviders(),
  });
