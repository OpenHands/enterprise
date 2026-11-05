import { useMutation, useQueryClient } from "@tanstack/react-query";
import { oauthV2Service } from "#/api/oauth-v2-service/oauth-v2-service.api";
import { OAUTH_V2_GIT_PROVIDERS_QUERY_KEY } from "#/hooks/query/use-oauth-v2-git-providers";

export const useLinkOAuthV2GitProvider = () =>
  useMutation({
    mutationFn: (providerId: number) =>
      oauthV2Service.startLinkGitProvider(providerId),
    onSuccess: (startUrl) => {
      window.location.href = startUrl;
    },
    meta: { disableToast: true },
  });

export const useUnlinkOAuthV2GitProvider = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (providerId: number) =>
      oauthV2Service.unlinkGitProvider(providerId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: OAUTH_V2_GIT_PROVIDERS_QUERY_KEY,
      });
    },
    meta: { disableToast: true },
  });
};
