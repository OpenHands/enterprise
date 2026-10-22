import { useQuery } from "@tanstack/react-query";
import { oauthV2Service } from "#/api/oauth-v2-service/oauth-v2-service.api";

export const OAUTH_V2_GIT_PROVIDERS_QUERY_KEY = ["oauth-v2", "git-providers"];

export const useOAuthV2GitProviders = (enabled: boolean) =>
  useQuery({
    queryKey: OAUTH_V2_GIT_PROVIDERS_QUERY_KEY,
    queryFn: () => oauthV2Service.listGitProviders(),
    enabled,
  });
