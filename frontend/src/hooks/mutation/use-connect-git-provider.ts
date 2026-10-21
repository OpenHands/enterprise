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
      let reauthenticationProvider: string | null = null;
      try {
        const context = await AuthService.getKeycloakLinkContext();
        reauthenticationProvider = context.provider;
      } catch {
        // Older servers and temporary lookup failures must not block Connect.
        // Without a server hint, retain the existing saved-login routing.
      }
      return generateIdpLinkUrl(
        provider,
        requestUrl,
        authUrl,
        reauthenticationProvider,
      );
    },
    meta: { disableToast: true },
  });
