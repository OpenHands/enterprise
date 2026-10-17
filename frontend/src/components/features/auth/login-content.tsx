import { useTranslation } from "react-i18next";
import { FaUserShield } from "react-icons/fa";
import { KeyRound } from "lucide-react";
import { I18nKey } from "#/i18n/declaration";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import GitHubLogo from "#/assets/branding/github-logo.svg?react";
import GitLabLogo from "#/assets/branding/gitlab-logo.svg?react";
import BitbucketLogo from "#/assets/branding/bitbucket-logo.svg?react";
import AzureDevOpsLogo from "#/assets/branding/azure-devops-logo.svg?react";
import { useAuthUrl } from "#/hooks/use-auth-url";
import { WebClientConfig } from "#/api/option-service/option.types";
import { Provider } from "#/types/settings";
import { PublicIdpProvider } from "#/api/auth-providers-service/auth-providers.types";
import { TermsAndPrivacyNotice } from "#/components/shared/terms-and-privacy-notice";
import { useRecaptcha } from "#/hooks/use-recaptcha";
import { useConfig } from "#/hooks/query/use-config";
import { displayErrorToast } from "#/utils/custom-toast-handlers";
import { cn } from "#/utils/utils";
import { LoginCTA } from "./login-cta";
import { useAppMode } from "#/hooks/use-app-mode";

const BUTTON_BASE_CLASSES =
  "w-[301.5px] h-10 rounded p-2 flex items-center justify-center cursor-pointer transition-opacity hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed";
const BUTTON_LABEL_CLASSES = "text-sm font-medium leading-5 px-1";

/** A single login IDP button, populated from its ``auth_providers`` row
 * (``GET /oauth/idp-providers``) rather than hard-coded per brand -- see
 * ``V2LoginProviders``. The icon is rendered as an ``<img>`` from a
 * ``data:image/svg+xml`` URI (never ``dangerouslySetInnerHTML``) so an
 * admin-supplied SVG can't execute script in an unauthenticated visitor's
 * browser: images loaded this way are rendered in "image mode", which
 * disables scripting. */
function V2ProviderButton({
  provider,
  returnTo,
}: {
  provider: PublicIdpProvider;
  returnTo: string;
}) {
  const href =
    returnTo && returnTo !== "/"
      ? `/oauth/${provider.id}/login?redirect_url=${encodeURIComponent(returnTo)}`
      : `/oauth/${provider.id}/login`;
  const iconSrc = provider.icon
    ? `data:image/svg+xml;utf8,${encodeURIComponent(provider.icon)}`
    : null;

  return (
    <a
      href={href}
      data-testid={`v2-idp-provider-${provider.id}`}
      className={cn(BUTTON_BASE_CLASSES, "text-white")}
      style={{ backgroundColor: provider.color }}
    >
      {iconSrc ? (
        <img src={iconSrc} alt="" width={14} height={14} className="shrink-0" />
      ) : (
        <KeyRound size={14} className="shrink-0" />
      )}
      <span className={BUTTON_LABEL_CLASSES}>{provider.title}</span>
    </a>
  );
}

/** Renders the OAuth v2 login path's "Let's Get Started" buttons, one per
 * configured login IDP row -- closing the gap with the legacy (Keycloak-era)
 * login page below, which always showed a chooser instead of silently
 * redirecting to a single "default" provider (``GET /oauth/idp-login``). */
function V2LoginProviders({
  providers,
  returnTo,
}: {
  providers: PublicIdpProvider[];
  returnTo: string;
}) {
  const { t } = useTranslation();

  if (providers.length === 0) {
    return (
      <div className="text-center p-4 text-muted-foreground">
        {t(I18nKey.AUTH$NO_PROVIDERS_CONFIGURED)}
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center gap-3">
      {providers.map((provider) => (
        <V2ProviderButton
          key={provider.id}
          provider={provider}
          returnTo={returnTo}
        />
      ))}
    </div>
  );
}

export interface LoginContentProps {
  githubAuthUrl: string | null;
  appMode?: WebClientConfig["app_mode"] | null;
  authUrl?: WebClientConfig["auth_url"];
  providersConfigured?: Provider[];
  emailVerified?: boolean;
  hasDuplicatedEmail?: boolean;
  recaptchaBlocked?: boolean;
  accountDisabled?: boolean;
  gitProviderLoginFailed?: boolean;
  hasInvitation?: boolean;
  buildOAuthStateData?: (
    baseStateData: Record<string, string>,
  ) => Record<string, string>;
  /** Login IDPs configured in ``auth_providers``, from the public
   *  ``GET /oauth/idp-providers`` endpoint. When provided (even as an empty
   *  array), the OAuth v2 provider buttons render instead of the legacy,
   *  hard-coded-per-brand ones below -- see ``#/routes/login``, which only
   *  fetches and passes this when ``ENABLE_OAUTH_V2_LOGIN`` is on. */
  v2IdpProviders?: PublicIdpProvider[] | null;
  /** Relative path to forward as ``redirect_url`` to ``/oauth/{id}/login``
   *  once a v2 provider button is picked -- see ``v2IdpProviders``. */
  v2ReturnTo?: string;
}

export function LoginContent({
  githubAuthUrl,
  appMode,
  authUrl,
  providersConfigured,
  emailVerified = false,
  hasDuplicatedEmail = false,
  recaptchaBlocked = false,
  accountDisabled = false,
  gitProviderLoginFailed = false,
  hasInvitation = false,
  buildOAuthStateData,
  v2IdpProviders,
  v2ReturnTo = "",
}: LoginContentProps) {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const { isEnterpriseCloud } = useAppMode();

  // reCAPTCHA - only need token generation, verification happens at backend callback
  const { isReady: recaptchaReady, executeRecaptcha } = useRecaptcha({
    siteKey: config?.recaptcha_site_key ?? undefined,
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

  const buttonBaseClasses = BUTTON_BASE_CLASSES;
  const buttonLabelClasses = BUTTON_LABEL_CLASSES;

  // ``v2IdpProviders`` is only ever passed (even as `[]`) when the caller
  // (``#/routes/login``) has already confirmed ENABLE_OAUTH_V2_LOGIN is on
  // -- ``undefined``/``null`` here always means "render the legacy buttons
  // below", never "still loading" (the caller handles its own loading state
  // before rendering this component at all).
  const isV2Login = v2IdpProviders != null;

  // The "Bitbucket registrations disabled" copy only applies to the legacy,
  // hard-coded-per-brand Bitbucket button below -- in v2 mode, sign-up
  // eligibility is per-row via ``AuthProvider.allow_registration`` instead
  // of this one Bitbucket-specific message, so it must never render
  // alongside the v2 provider buttons.
  const showBitbucketSignupDisabled = showBitbucket && !isV2Login;

  const shouldShownHelperText =
    emailVerified ||
    hasDuplicatedEmail ||
    recaptchaBlocked ||
    accountDisabled ||
    gitProviderLoginFailed ||
    hasInvitation ||
    showBitbucketSignupDisabled;

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

        <h1 className="text-[39px] leading-5 font-medium text-white text-center">
          {t(I18nKey.AUTH$LETS_GET_STARTED)}
        </h1>

        {shouldShownHelperText && (
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
            {accountDisabled && (
              <p className="text-sm text-danger text-center max-w-125">
                {t(I18nKey.AUTH$ACCOUNT_DISABLED)}
              </p>
            )}
            {gitProviderLoginFailed && (
              <p className="text-sm text-danger text-center max-w-125">
                {t(I18nKey.AUTH$GIT_PROVIDER_LOGIN_FAILED)}
              </p>
            )}
            {hasInvitation && (
              <p className="text-sm text-muted-foreground text-center">
                {t(I18nKey.AUTH$INVITATION_PENDING)}
              </p>
            )}
            {showBitbucketSignupDisabled && (
              <p className="text-sm text-white text-center max-w-125">
                {t(I18nKey.AUTH$BITBUCKET_SIGNUP_DISABLED)}
              </p>
            )}
          </div>
        )}

        {isV2Login ? (
          <V2LoginProviders
            providers={v2IdpProviders ?? []}
            returnTo={v2ReturnTo}
          />
        ) : (
          <div className="flex flex-col items-center gap-3">
            {noProvidersConfigured ? (
              <div className="text-center p-4 text-muted-foreground">
                {t(I18nKey.AUTH$NO_PROVIDERS_CONFIGURED)}
              </div>
            ) : (
              <>
                {showGithub && (
                  <button
                    type="button"
                    onClick={handleGitHubAuth}
                    className={`${buttonBaseClasses} bg-[#9E28B0] text-white`}
                  >
                    <GitHubLogo width={14} height={14} className="shrink-0" />
                    <span className={buttonLabelClasses}>
                      {t(I18nKey.GITHUB$CONNECT_TO_GITHUB)}
                    </span>
                  </button>
                )}

                {showGitlab && (
                  <button
                    type="button"
                    onClick={handleGitLabAuth}
                    className={`${buttonBaseClasses} bg-[#FC6B0E] text-white`}
                  >
                    <GitLabLogo width={14} height={14} className="shrink-0" />
                    <span className={buttonLabelClasses}>
                      {t(I18nKey.GITLAB$CONNECT_TO_GITLAB)}
                    </span>
                  </button>
                )}

                {showBitbucket && (
                  <button
                    type="button"
                    onClick={handleBitbucketAuth}
                    className={`${buttonBaseClasses} bg-[#2684FF] text-white`}
                  >
                    <BitbucketLogo
                      width={14}
                      height={14}
                      className="shrink-0"
                    />
                    <span className={buttonLabelClasses}>
                      {t(I18nKey.BITBUCKET$CONNECT_TO_BITBUCKET)}
                    </span>
                  </button>
                )}

                {showBitbucketDataCenter && (
                  <button
                    type="button"
                    onClick={handleBitbucketDataCenterAuth}
                    className={`${buttonBaseClasses} bg-[#2684FF] text-white`}
                  >
                    <BitbucketLogo
                      width={14}
                      height={14}
                      className="shrink-0"
                    />
                    <span className={buttonLabelClasses}>
                      {t(
                        I18nKey.BITBUCKET_DATA_CENTER$CONNECT_TO_BITBUCKET_DATA_CENTER,
                      )}
                    </span>
                  </button>
                )}

                {showAzureDevOps && (
                  <button
                    type="button"
                    onClick={handleAzureDevOpsAuth}
                    className={`${buttonBaseClasses} bg-[#0078D4] text-white`}
                  >
                    <AzureDevOpsLogo
                      width={14}
                      height={14}
                      className="shrink-0"
                    />
                    <span className={buttonLabelClasses}>
                      {t(I18nKey.AZURE_DEVOPS$CONNECT_ACCOUNT)}
                    </span>
                  </button>
                )}

                {showEnterpriseSso && (
                  <button
                    type="button"
                    onClick={handleEnterpriseSsoAuth}
                    className={`${buttonBaseClasses} bg-[#374151] text-white`}
                  >
                    <FaUserShield size={14} className="shrink-0" />
                    <span className={buttonLabelClasses}>
                      {t(I18nKey.ENTERPRISE_SSO$CONNECT_TO_ENTERPRISE_SSO)}
                    </span>
                  </button>
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
