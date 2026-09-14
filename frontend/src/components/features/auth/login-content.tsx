import type { ReactNode, JSX } from "react";
import { useTranslation } from "react-i18next";
import { FaUserShield } from "react-icons/fa";
import { LoginButton, loginButtonLabelClassName } from "./login-button";
import { I18nKey } from "#/i18n/declaration";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import GitHubLogo from "#/assets/branding/github-logo.svg?react";
import GitLabLogo from "#/assets/branding/gitlab-logo.svg?react";
import BitbucketLogo from "#/assets/branding/bitbucket-logo.svg?react";
import AzureDevOpsLogo from "#/assets/branding/azure-devops-logo.svg?react";
import { useAuthUrl } from "#/hooks/use-auth-url";
import { WebClientConfig } from "#/api/option-service/option.types";
import { Provider } from "#/types/settings";
import { TermsAndPrivacyNotice } from "#/components/shared/terms-and-privacy-notice";
import { useRecaptcha } from "#/hooks/use-recaptcha";
import { useConfig } from "#/hooks/query/use-config";
import { displayErrorToast } from "#/utils/custom-toast-handlers";
import { cn } from "#/utils/utils";
import { LoginCTA } from "./login-cta";
import { useAppMode } from "#/hooks/use-app-mode";

export interface LoginContentProps {
  githubAuthUrl?: string | null;
  title?: string;
  children?: ReactNode;
  appMode?: WebClientConfig["app_mode"] | null;
  authUrl?: WebClientConfig["auth_url"];
  providersConfigured?: Provider[];
  emailVerified?: boolean;
  hasDuplicatedEmail?: boolean;
  recaptchaBlocked?: boolean;
  hasInvitation?: boolean;
  buildOAuthStateData?: (
    baseStateData: Record<string, string>,
  ) => Record<string, string>;
}

export function LoginContent({
  githubAuthUrl,
  title,
  children,
  appMode,
  authUrl,
  providersConfigured,
  emailVerified = false,
  hasDuplicatedEmail = false,
  recaptchaBlocked = false,
  hasInvitation = false,
  buildOAuthStateData,
}: LoginContentProps): JSX.Element {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const { isEnterpriseCloud } = useAppMode();

  // reCAPTCHA - only need token generation, verification happens at backend callback
  const { isReady: recaptchaReady, executeRecaptcha } = useRecaptcha({
    siteKey: children ? undefined : (config?.recaptcha_site_key ?? undefined),
  });

  const gitlabAuthUrl = useAuthUrl({
    appMode: appMode || null,
    identityProvider: "gitlab",
    authUrl,
  });

  const bitbucketAuthUrl = useAuthUrl({
    appMode: appMode || null,
    identityProvider: "bitbucket",
    authUrl,
  });

  const bitbucketDataCenterAuthUrl = useAuthUrl({
    appMode: appMode || null,
    identityProvider: "bitbucket_data_center",
    authUrl,
  });

  const azureDevOpsAuthUrl = useAuthUrl({
    appMode: appMode || null,
    identityProvider: "azure_devops",
    authUrl,
  });

  const enterpriseSsoAuthUrl = useAuthUrl({
    appMode: appMode || null,
    identityProvider: "enterprise_sso",
    authUrl,
  });

  const handleAuthRedirect = async (redirectUrl: string) => {
    const url = new URL(redirectUrl);
    const currentState =
      url.searchParams.get("state") || window.location.origin;

    // Build base state data
    let stateData: Record<string, string> = {
      redirect_url: currentState,
    };

    // Add invitation token if present
    if (buildOAuthStateData) {
      stateData = buildOAuthStateData(stateData);
    }

    // If reCAPTCHA is configured, add token to state
    if (config?.recaptcha_site_key && recaptchaReady) {
      try {
        const token = await executeRecaptcha("LOGIN");
        if (token) {
          stateData.recaptcha_token = token;
        }
      } catch (err) {
        displayErrorToast(t(I18nKey.AUTH$RECAPTCHA_BLOCKED));
        return;
      }
    }

    // Encode state and redirect
    url.searchParams.set("state", btoa(JSON.stringify(stateData)));
    window.location.href = url.toString();
  };

  const handleGitHubAuth = () => {
    if (githubAuthUrl) {
      handleAuthRedirect(githubAuthUrl);
    }
  };

  const handleGitLabAuth = () => {
    if (gitlabAuthUrl) {
      handleAuthRedirect(gitlabAuthUrl);
    }
  };

  const handleBitbucketAuth = () => {
    if (bitbucketAuthUrl) {
      handleAuthRedirect(bitbucketAuthUrl);
    }
  };

  const handleBitbucketDataCenterAuth = () => {
    if (bitbucketDataCenterAuthUrl) {
      handleAuthRedirect(bitbucketDataCenterAuthUrl);
    }
  };

  const handleAzureDevOpsAuth = () => {
    if (azureDevOpsAuthUrl) {
      handleAuthRedirect(azureDevOpsAuthUrl);
    }
  };

  const handleEnterpriseSsoAuth = () => {
    if (enterpriseSsoAuthUrl) {
      handleAuthRedirect(enterpriseSsoAuthUrl);
    }
  };

  const showGithub =
    providersConfigured &&
    providersConfigured.length > 0 &&
    providersConfigured.includes("github");
  const showGitlab =
    providersConfigured &&
    providersConfigured.length > 0 &&
    providersConfigured.includes("gitlab");
  const showBitbucket =
    providersConfigured &&
    providersConfigured.length > 0 &&
    providersConfigured.includes("bitbucket");
  const showBitbucketDataCenter =
    providersConfigured &&
    providersConfigured.length > 0 &&
    providersConfigured.includes("bitbucket_data_center");
  const showAzureDevOps =
    providersConfigured &&
    providersConfigured.length > 0 &&
    providersConfigured.includes("azure_devops");
  const showEnterpriseSso =
    providersConfigured &&
    providersConfigured.length > 0 &&
    providersConfigured.includes("enterprise_sso");

  const noProvidersConfigured =
    !providersConfigured || providersConfigured.length === 0;

  const shouldShownHelperText =
    emailVerified ||
    hasDuplicatedEmail ||
    recaptchaBlocked ||
    hasInvitation ||
    showBitbucket;

  return (
    <div
      className={cn(
        "flex flex-col md:flex-row items-center md:items-stretch gap-6 h-full",
      )}
    >
      <div
        className={cn("flex flex-col items-center w-full gap-12.5")}
        data-testid="login-content"
      >
        <div>
          <OpenHandsLogoWhite width={106} height={72} />
        </div>

        <h1
          className={cn(
            "text-[39px] font-medium text-white text-center",
            title ? "leading-tight" : "leading-5",
          )}
        >
          {title ?? t(I18nKey.AUTH$LETS_GET_STARTED)}
        </h1>

        {!children && shouldShownHelperText && (
          <div className="flex flex-col items-center gap-3">
            {emailVerified && (
              <p className="text-sm text-muted-foreground text-center">
                {t(I18nKey.AUTH$EMAIL_VERIFIED_PLEASE_LOGIN)}
              </p>
            )}
            {hasDuplicatedEmail && (
              <p className="text-sm text-danger text-center">
                {t(I18nKey.AUTH$DUPLICATE_EMAIL_ERROR)}
              </p>
            )}
            {recaptchaBlocked && (
              <p className="text-sm text-danger text-center max-w-125">
                {t(I18nKey.AUTH$RECAPTCHA_BLOCKED)}
              </p>
            )}
            {hasInvitation && (
              <p className="text-sm text-muted-foreground text-center">
                {t(I18nKey.AUTH$INVITATION_PENDING)}
              </p>
            )}
            {showBitbucket && (
              <p className="text-sm text-white text-center max-w-125">
                {t(I18nKey.AUTH$BITBUCKET_SIGNUP_DISABLED)}
              </p>
            )}
          </div>
        )}

        {children ? (
          <div className="w-[301.5px] max-w-full flex flex-col gap-4">
            {children}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3">
            {noProvidersConfigured ? (
              <div className="text-center p-4 text-muted-foreground">
                {t(I18nKey.AUTH$NO_PROVIDERS_CONFIGURED)}
              </div>
            ) : (
              <>
                {showGithub && (
                  <LoginButton
                    type="button"
                    onClick={handleGitHubAuth}
                    className="bg-[#9E28B0] text-white"
                  >
                    <GitHubLogo width={14} height={14} className="shrink-0" />
                    <span className={loginButtonLabelClassName}>
                      {t(I18nKey.GITHUB$CONNECT_TO_GITHUB)}
                    </span>
                  </LoginButton>
                )}

                {showGitlab && (
                  <LoginButton
                    type="button"
                    onClick={handleGitLabAuth}
                    className="bg-[#FC6B0E] text-white"
                  >
                    <GitLabLogo width={14} height={14} className="shrink-0" />
                    <span className={loginButtonLabelClassName}>
                      {t(I18nKey.GITLAB$CONNECT_TO_GITLAB)}
                    </span>
                  </LoginButton>
                )}

                {showBitbucket && (
                  <LoginButton
                    type="button"
                    onClick={handleBitbucketAuth}
                    className="bg-[#2684FF] text-white"
                  >
                    <BitbucketLogo
                      width={14}
                      height={14}
                      className="shrink-0"
                    />
                    <span className={loginButtonLabelClassName}>
                      {t(I18nKey.BITBUCKET$CONNECT_TO_BITBUCKET)}
                    </span>
                  </LoginButton>
                )}

                {showBitbucketDataCenter && (
                  <LoginButton
                    type="button"
                    onClick={handleBitbucketDataCenterAuth}
                    className="bg-[#2684FF] text-white"
                  >
                    <BitbucketLogo
                      width={14}
                      height={14}
                      className="shrink-0"
                    />
                    <span className={loginButtonLabelClassName}>
                      {t(
                        I18nKey.BITBUCKET_DATA_CENTER$CONNECT_TO_BITBUCKET_DATA_CENTER,
                      )}
                    </span>
                  </LoginButton>
                )}

                {showAzureDevOps && (
                  <LoginButton
                    type="button"
                    onClick={handleAzureDevOpsAuth}
                    className="bg-[#0078D4] text-white"
                  >
                    <AzureDevOpsLogo
                      width={14}
                      height={14}
                      className="shrink-0"
                    />
                    <span className={loginButtonLabelClassName}>
                      {t(I18nKey.AZURE_DEVOPS$CONNECT_ACCOUNT)}
                    </span>
                  </LoginButton>
                )}

                {showEnterpriseSso && (
                  <LoginButton
                    type="button"
                    onClick={handleEnterpriseSsoAuth}
                    className="bg-[#374151] text-white"
                  >
                    <FaUserShield size={14} className="shrink-0" />
                    <span className={loginButtonLabelClassName}>
                      {t(I18nKey.ENTERPRISE_SSO$CONNECT_TO_ENTERPRISE_SSO)}
                    </span>
                  </LoginButton>
                )}
              </>
            )}
          </div>
        )}

        <TermsAndPrivacyNotice className="max-w-[320px] text-[#A3A3A3]" />
      </div>

      {isEnterpriseCloud && <LoginCTA />}
    </div>
  );
}
