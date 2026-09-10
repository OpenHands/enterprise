import React from "react";
import { useNavigate, useSearchParams, useLocation } from "react-router";
import { useTranslation } from "react-i18next";
import { useIsAuthed } from "#/hooks/query/use-is-authed";
import { useConfig } from "#/hooks/query/use-config";
import { useGitHubAuthUrl } from "#/hooks/use-github-auth-url";
import { useEmailVerification } from "#/hooks/use-email-verification";
import { useInvitation } from "#/hooks/use-invitation";
import { LoginContent } from "#/components/features/auth/login-content";
import { EmailVerificationModal } from "#/components/features/waitlist/email-verification-modal";
import { RequestSubmittedModal } from "#/components/features/onboarding/request-submitted-modal";
import { navigateOrHardRedirect } from "#/utils/cross-app-redirect";
import { useAuthCapabilities } from "#/hooks/query/use-auth-capabilities";
import { PasswordLogin } from "#/components/features/auth/password-login";
import { getAuthReturnTo } from "#/utils/auth-redirect";
import { I18nKey } from "#/i18n/declaration";

interface LocationState {
  showRequestSubmittedModal?: boolean;
}

export function getSafeReturnTo(searchParams: URLSearchParams): string {
  return getAuthReturnTo(searchParams);
}

export default function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const returnTo = getSafeReturnTo(searchParams);
  const locationState = location.state as LocationState | null;

  const config = useConfig();
  const capabilities = useAuthCapabilities();
  const { data: isAuthed, isLoading: isAuthLoading } = useIsAuthed();
  const {
    emailVerified,
    hasDuplicatedEmail,
    recaptchaBlocked,
    wasRateLimited,
    emailVerificationModalOpen,
    setEmailVerificationModalOpen,
    userId,
  } = useEmailVerification();

  const { invitationToken, hasInvitation, buildOAuthStateData } =
    useInvitation();

  const gitHubAuthUrl = useGitHubAuthUrl({
    appMode: config.data?.app_mode || null,
    authUrl: config.data?.auth_url,
  });

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

  if (isAuthLoading || config.isLoading || capabilities.isLoading) {
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

  if (!capabilities.data || config.isError) {
    return (
      <main className="min-h-screen flex flex-col items-center justify-center gap-4 bg-base p-4">
        <p role="alert">{t(I18nKey.AUTH$UNAVAILABLE)}</p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="underline"
        >
          {t(I18nKey.AUTH$RETRY)}
        </button>
      </main>
    );
  }

  return (
    <>
      <main
        className="min-h-screen flex items-center justify-center bg-base p-4"
        data-testid="login-page"
      >
        {capabilities.data.mode === "local" ? (
          <PasswordLogin
            returnTo={returnTo}
            invitationToken={invitationToken}
            emailRecovery={capabilities.data.email_recovery}
          />
        ) : (
          <LoginContent
            githubAuthUrl={gitHubAuthUrl}
            appMode={config.data?.app_mode}
            authUrl={config.data?.auth_url}
            providersConfigured={capabilities.data.login_providers}
            emailVerified={emailVerified}
            hasDuplicatedEmail={hasDuplicatedEmail}
            recaptchaBlocked={recaptchaBlocked}
            hasInvitation={hasInvitation}
            buildOAuthStateData={buildOAuthStateData}
          />
        )}
      </main>

      {capabilities.data.mode === "keycloak" && emailVerificationModalOpen && (
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
