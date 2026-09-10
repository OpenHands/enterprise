import { generateAuthUrl } from "#/utils/generate-auth-url";
import { WebClientConfig } from "#/api/option-service/option.types";
import { useAuthCapabilities } from "#/hooks/query/use-auth-capabilities";

interface UseAuthUrlConfig {
  appMode: WebClientConfig["app_mode"] | null;
  identityProvider: string;
  authUrl?: WebClientConfig["auth_url"];
}

export const useAuthUrl = (config: UseAuthUrlConfig) => {
  const { data: capabilities } = useAuthCapabilities();
  if (
    config.appMode === "saas" &&
    capabilities?.mode === "keycloak" &&
    capabilities.login_providers.includes(config.identityProvider)
  ) {
    return generateAuthUrl(
      config.identityProvider,
      new URL(window.location.href),
    );
  }

  return null;
};
