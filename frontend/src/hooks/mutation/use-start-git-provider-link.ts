import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import OAuthService from "#/api/oauth-service/oauth-service.api";
import { useConfig } from "#/hooks/query/use-config";
import { I18nKey } from "#/i18n/declaration";
import { Provider } from "#/types/settings";
import { generateIdpLinkUrl } from "#/utils/generate-idp-link-url";
import { getProviderName } from "#/utils/utils";

/** Starts linking a git provider through the flow the backend picks for this
 * session (OAuth v2 or Keycloak) and sends the browser there. */
export const useStartGitProviderLink = () => {
  const { t } = useTranslation();
  const { data: config } = useConfig();

  return useMutation({
    mutationFn: async (provider: Provider) => {
      const { flow, provider_id: providerId } =
        await OAuthService.getLinkFlow(provider);
      if (flow === "keycloak") {
        return generateIdpLinkUrl(
          provider,
          new URL(window.location.href),
          config?.auth_url,
        );
      }
      if (providerId === null) {
        throw new Error(
          t(I18nKey.SETTINGS$GIT_PROVIDER_NOT_CONFIGURED, {
            provider: getProviderName(provider),
          }),
        );
      }
      return OAuthService.startLink(providerId);
    },
    onSuccess: (startUrl) => {
      window.location.href = startUrl;
    },
    meta: {
      disableToast: true,
    },
  });
};
