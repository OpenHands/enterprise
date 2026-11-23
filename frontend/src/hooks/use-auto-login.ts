import { useEffect } from "react";
import { useConfig } from "./query/use-config";
import { useIsAuthed } from "./query/use-is-authed";
import { getLoginMethod, LoginMethod } from "#/utils/local-storage";
import { useAuthUrl } from "./use-auth-url";
import { useIsOnIntermediatePage } from "./use-is-on-intermediate-page";

/**
 * Hook to automatically log in the user if they have a login method stored in local storage
 * Only works in SAAS mode and when the user is not already logged in
 */
export const useAutoLogin = () => {
  const { data: config, isLoading: isConfigLoading } = useConfig();
  const { data: isAuthed, isLoading: isAuthLoading } = useIsAuthed();
  const isOnIntermediatePage = useIsOnIntermediatePage();

  const loginMethod = getLoginMethod();

  const githubAuthUrl = useAuthUrl({
    appMode: config?.app_mode || null,
    identityProvider: "github",
    authUrl: config?.auth_url,
  });

  const gitlabAuthUrl = useAuthUrl({
    appMode: config?.app_mode || null,
    identityProvider: "gitlab",
    authUrl: config?.auth_url,
  });

  const bitbucketAuthUrl = useAuthUrl({
    appMode: config?.app_mode || null,
    identityProvider: "bitbucket",
    authUrl: config?.auth_url,
  });

  const bitbucketDataCenterUrl = useAuthUrl({
    appMode: config?.app_mode || null,
    identityProvider: "bitbucket_data_center",
    authUrl: config?.auth_url,
  });

  const azureDevOpsUrl = useAuthUrl({
    appMode: config?.app_mode || null,
    identityProvider: "azure_devops",
    authUrl: config?.auth_url,
  });

  const enterpriseSsoUrl = useAuthUrl({
    appMode: config?.app_mode || null,
    identityProvider: "enterprise_sso",
    authUrl: config?.auth_url,
  });

  useEffect(() => {
    if (config?.app_mode !== "saas") {
      return;
    }

    if (isConfigLoading || isAuthLoading) {
      return;
    }

    // Don't auto-login from intermediate steps in the auth flow
    if (isOnIntermediatePage) {
      return;
    }

    if (isAuthed) {
      return;
    }

    if (!loginMethod) {
      return;
    }

    let authUrl: string | null = null;
    if (loginMethod === LoginMethod.GITHUB) {
      authUrl = githubAuthUrl;
    } else if (loginMethod === LoginMethod.GITLAB) {
      authUrl = gitlabAuthUrl;
    } else if (loginMethod === LoginMethod.BITBUCKET) {
      authUrl = bitbucketAuthUrl;
    } else if (loginMethod === LoginMethod.BITBUCKET_DATA_CENTER) {
      authUrl = bitbucketDataCenterUrl;
    } else if (loginMethod === LoginMethod.AZURE_DEVOPS) {
      authUrl = azureDevOpsUrl;
    } else if (loginMethod === LoginMethod.ENTERPRISE_SSO) {
      authUrl = enterpriseSsoUrl;
    }

    if (authUrl) {
      const url = new URL(authUrl);
      url.searchParams.append("login_method", loginMethod);

      // After successful login, the user will be redirected back and can navigate to the last page
      window.location.href = url.toString();
    }
  }, [
    config?.app_mode,
    isAuthed,
    isConfigLoading,
    isAuthLoading,
    isOnIntermediatePage,
    loginMethod,
    githubAuthUrl,
    gitlabAuthUrl,
    bitbucketAuthUrl,
    bitbucketDataCenterUrl,
    azureDevOpsUrl,
    enterpriseSsoUrl,
  ]);
};
