import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useIsAuthed } from "#/hooks/query/use-is-authed";
import {
  AuthFrame,
  AuthError,
} from "#/components/features/native-auth/auth-form";
import { NativeSignIn } from "#/components/features/native-auth/native-sign-in";
import { useConfig } from "#/hooks/query/use-config";
import { getSafeReturnTo } from "#/utils/safe-return-to";
import { navigateOrHardRedirect } from "#/utils/cross-app-redirect";

export default function NativeLoginPage(): React.JSX.Element | null {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { data: config } = useConfig();
  const returnTo = getSafeReturnTo(params);
  const { data: isAuthed, acceptedTos, isLoading } = useIsAuthed();
  useEffect(() => {
    if (isAuthed) {
      const destination =
        acceptedTos === false
          ? `/accept-tos?redirect_url=${encodeURIComponent(returnTo)}`
          : returnTo;
      navigateOrHardRedirect(navigate, destination, { replace: true });
    }
  }, [isAuthed, acceptedTos, returnTo, navigate]);
  if (isLoading)
    return (
      <main className="min-h-screen flex items-center justify-center bg-base">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
      </main>
    );
  if (isAuthed) return null;
  return (
    <AuthFrame>
      <AuthError
        message={params.has("sso_error") ? t("NATIVE_AUTH$SSO_FAILED") : null}
      />
      <NativeSignIn methods={config?.login_methods} returnTo={returnTo} />
    </AuthFrame>
  );
}
