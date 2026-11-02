import { useQuery } from "@tanstack/react-query";
import { oauthV2Service } from "#/api/oauth-v2-service/oauth-v2-service.api";

export const oauthV2GitProviderCheckQueryKey = (providerId: number) => [
  "oauth-v2",
  "git-provider-check",
  providerId,
];

/** Tests a linked git provider's token each time the card mounts; call
 * `refetch` to test again on demand. */
export const useOAuthV2GitProviderCheck = (
  providerId: number,
  enabled: boolean,
) =>
  useQuery({
    queryKey: oauthV2GitProviderCheckQueryKey(providerId),
    queryFn: () => oauthV2Service.checkGitProvider(providerId),
    enabled,
    // Each check is a call to the git provider: only on mount or on request.
    refetchOnWindowFocus: false,
    retry: false,
    meta: { disableToast: true },
  });
