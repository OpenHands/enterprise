import { useMutation } from "@tanstack/react-query";
import AuthService from "#/api/auth-service/auth-service.api";
import { generateIdpLinkUrl } from "#/utils/generate-idp-link-url";
import { Provider } from "#/types/settings";

export const useConnectGitProvider = () =>
  useMutation({
    mutationFn: async ({
      provider,
      requestUrl,
      authUrl,
    }: {
      provider: Provider;
      requestUrl: URL;
      authUrl?: string | null;
    }) => {
      const context = await AuthService.getKeycloakLinkContext();
      return generateIdpLinkUrl(
        provider,
        requestUrl,
        authUrl,
        context.provider,
      );
    },
    meta: { disableToast: true },
  });
