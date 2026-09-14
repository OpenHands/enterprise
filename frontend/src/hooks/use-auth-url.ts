import { getAuthentication } from "#/api/auth-adapter";
import { generateAuthUrl } from "#/utils/generate-auth-url";
import { WebClientConfig } from "#/api/option-service/option.types";

interface UseAuthUrlConfig {
  appMode: WebClientConfig["app_mode"] | null;
  identityProvider: string;
  authUrl?: WebClientConfig["auth_url"];
}

export const useAuthUrl = (config: UseAuthUrlConfig): string | null => {
  if (getAuthentication().providerLoginEnabled(config.appMode)) {
    return generateAuthUrl(
      config.identityProvider,
      new URL(window.location.href),
      config.authUrl,
    );
  }

  return null;
};
