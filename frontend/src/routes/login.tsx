import React from "react";
import { useNavigate, useSearchParams, useLocation } from "react-router";
import { useIsAuthed } from "#/hooks/query/use-is-authed";
import { useConfig } from "#/hooks/query/use-config";
import { useGitHubAuthUrl } from "#/hooks/use-github-auth-url";
import { useEmailVerification } from "#/hooks/use-email-verification";
import { useInvitation } from "#/hooks/use-invitation";
import { usePublicIdpProviders } from "#/hooks/query/use-auth-providers";
import { LoginContent } from "#/components/features/auth/login-content";
import { EmailVerificationModal } from "#/components/features/waitlist/email-verification-modal";
import { RequestSubmittedModal } from "#/components/features/onboarding/request-submitted-modal";
import { navigateOrHardRedirect } from "#/utils/cross-app-redirect";

interface LocationState {
  showRequestSubmittedModal?: boolean;
}

export function getSafeReturnTo(searchParams: URLSearchParams): string {
  const destination =
    searchParams.get("returnTo") || searchParams.get("redirect") || "/";
  if (!destination.startsWith("/") || destination.startsWith("//")) {
    return "/";
  }
  return destination;
}

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const returnTo = getSafeReturnTo(searchParams);
  const locationState = location.state as LocationState | null;

  const config = useConfig();
  const { data: isAuthed, isLoading: isAuthLoading } = useIsAuthed();
  const {
    emailVerified,
    hasDuplicatedEmail,
    recaptchaBlocked,
    accountDisabled,
    wasRateLimited,
    emailVerificationModalOpen,
    setEmailVerificationModalOpen,
    userId,
  } = useEmailVerification();

  const { hasInvitation, buildOAuthStateData } = useInvitation();

  const gitHubAuthUrl = useGitHubAuthUrl({
    appMode: config.data?.app_mode || null,
    authUrl: config.data?.auth_url,
  });

  const enableOauthV2Login =
    !!config.data?.feature_flags?.enable_oauth_v2_login;
  const { data: v2IdpProviders, isLoading: v2ProvidersLoading } =
    usePublicIdpProviders(enableOauthV2Login);

  // Mirrors the single-provider convenience of the backend's
  // `/oauth/idp-login` shortcut: when there's exactly one configured login
  // IDP, skip the chooser screen and go straight to it rather than making
  // the user click a single button.
  const singleV2Provider =
    enableOauthV2Login && v2IdpProviders?.length === 1
      ? v2IdpProviders[0]
      : null;

  React.useEffect(() => {
    if (singleV2Provider) {
      const target =
        returnTo && returnTo !== "/"
          ? `/oauth/${singleV2Provider.id}/login?redirect_url=${encodeURIComponent(returnTo)}`
          : `/oauth/${singleV2Provider.id}/login`;
      window.location.href = target;
    }
  }, [singleV2Provider, returnTo]);

  const [showRequestModal, setShowRequestModal] = React.useState(
    () => locationState?.showRequestSubmittedModal ?? false,
  );

  const handleRequestModalClose = () => {
    setShowRequestModal(false);
    navigate(location.pathname, { replace: true, state: {} });
  };

  // Redirect OSS mode users to home
  React.useEffect(() => {
    if (!config.isLoading && config.data?.app_mode === "oss") {
      navigate("/", { replace: true });
    }
  }, [config.isLoading, config.data?.app_mode, navigate]);

  // Redirect authenticated users away from login page
  // Preserve login_method param so useAuthCallback can store it for auto-login
  React.useEffect(() => {
    if (!isAuthLoading && isAuthed) {
      const loginMethod = searchParams.get("login_method");
      let destination = returnTo;
      if (loginMethod) {
        const separator = returnTo.includes("?") ? "&" : "?";
        destination = `${returnTo}${separator}login_method=${encodeURIComponent(loginMethod)}`;
      }
      navigateOrHardRedirect(navigate, destination, { replace: true });
    }
  }, [isAuthed, isAuthLoading, navigate, returnTo, searchParams]);

  if (
    isAuthLoading ||
    config.isLoading ||
    (enableOauthV2Login && v2ProvidersLoading) ||
    singleV2Provider
  ) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-base">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
      </div>
    );
  }

  // Don't render login content if user is authenticated or in OSS mode
  if (isAuthed || config.data?.app_mode === "oss") {
    return null;
  }

  return (
    <>
      <main
        className="min-h-screen flex items-center justify-center bg-base p-4"
        data-testid="login-page"
      >
        <LoginContent
          githubAuthUrl={gitHubAuthUrl}
          appMode={config.data?.app_mode}
          authUrl={config.data?.auth_url}
          providersConfigured={config.data?.providers_configured}
          emailVerified={emailVerified}
          hasDuplicatedEmail={hasDuplicatedEmail}
          recaptchaBlocked={recaptchaBlocked}
          accountDisabled={accountDisabled}
          hasInvitation={hasInvitation}
          buildOAuthStateData={buildOAuthStateData}
          v2IdpProviders={enableOauthV2Login ? (v2IdpProviders ?? []) : null}
          v2ReturnTo={returnTo}
        />
      </main>

      {emailVerificationModalOpen && (
        <EmailVerificationModal
          onClose={() => {
            setEmailVerificationModalOpen(false);
          }}
          userId={userId}
          wasRateLimited={wasRateLimited}
        />
      )}

      {showRequestModal && (
        <RequestSubmittedModal onClose={handleRequestModalClose} />
      )}
    </>
  );
}
