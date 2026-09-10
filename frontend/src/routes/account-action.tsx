import React from "react";
import { useLocation, useParams, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useAuthCapabilities } from "#/hooks/query/use-auth-capabilities";
import { useInvitation } from "#/hooks/use-invitation";
import { useLogout } from "#/hooks/mutation/use-logout";
import {
  useChangePassword,
  useForgotPassword,
  useResetPassword,
  useEnrollAccount,
  useVerifyEmail,
  useRequestEmailVerification,
} from "#/hooks/mutation/use-local-auth";
import {
  AccountCard,
  AccountField,
  AccountSubmit,
  AccountError,
} from "#/components/features/auth/account-form";
import {
  authPageUrl,
  completeAuth,
  getAuthReturnTo,
} from "#/utils/auth-redirect";
import { isValidNewPassword } from "#/utils/password-policy";

const titles = {
  "change-password": I18nKey.AUTH$CHANGE_PASSWORD,
  "forgot-password": I18nKey.AUTH$FORGOT_PASSWORD,
  "reset-password": I18nKey.AUTH$RESET_PASSWORD,
  "verify-email": I18nKey.AUTH$VERIFY_EMAIL,
  enroll: I18nKey.AUTH$ENROLL_INVITATION,
};

export const meta = () => [{ name: "referrer", content: "no-referrer" }];

export default function AccountActionPage() {
  const { t } = useTranslation();
  const { action = "" } = useParams();
  const [params] = useSearchParams();
  const { hash } = useLocation();
  const { invitationToken } = useInvitation();
  const capabilities = useAuthCapabilities();
  const change = useChangePassword();
  const forgot = useForgotPassword();
  const reset = useResetPassword();
  const enroll = useEnrollAccount();
  const verify = useVerifyEmail();
  const requestVerification = useRequestEmailVerification();
  const logout = useLogout();
  const [mismatch, setMismatch] = React.useState(false);
  const [invalidPassword, setInvalidPassword] = React.useState(false);
  const returnTo = getAuthReturnTo(params);
  // Invitation cleanup may replace the query and hash. Retain the action token
  // only in this page's memory; query-string tokens are never accepted.
  const [token] = React.useState(() =>
    new URLSearchParams(hash.slice(1)).get("token"),
  );
  const context = {
    redirect_url: returnTo,
    invitation_token: invitationToken || undefined,
  };
  const isPasswordAction = [
    "change-password",
    "reset-password",
    "enroll",
  ].includes(action);
  const isPending =
    change.isPending ||
    forgot.isPending ||
    reset.isPending ||
    enroll.isPending ||
    verify.isPending ||
    requestVerification.isPending;
  const error =
    change.error ||
    forgot.error ||
    reset.error ||
    enroll.error ||
    verify.error ||
    requestVerification.error ||
    logout.error;

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("new_password") || "");
    setInvalidPassword(isPasswordAction && !isValidNewPassword(password));
    if (isPasswordAction && !isValidNewPassword(password)) return;
    if (isPasswordAction && password !== form.get("confirm_password")) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    if (action === "change-password")
      change.mutate(
        {
          ...context,
          current_password: String(form.get("current_password")),
          new_password: password,
        },
        { onSuccess: completeAuth },
      );
    else if (action === "forgot-password")
      forgot.mutate({ ...context, email: String(form.get("email")) });
    else if (action === "reset-password" && token)
      reset.mutate(
        { ...context, token, new_password: password },
        { onSuccess: completeAuth },
      );
    else if (action === "enroll" && invitationToken)
      enroll.mutate(
        { ...context, invitation_token: invitationToken, password },
        { onSuccess: completeAuth },
      );
    else if (action === "verify-email" && token)
      verify.mutate({ ...context, token }, { onSuccess: completeAuth });
    else if (action === "verify-email") requestVerification.mutate(context);
  };

  const isKnownAction = Object.hasOwn(titles, action);
  const missingToken =
    (action === "reset-password" && !token) ||
    (action === "enroll" && !invitationToken);
  const emailUnavailable =
    !capabilities.data?.email_recovery &&
    (action === "forgot-password" || (action === "verify-email" && !token));
  const title = isKnownAction
    ? t(titles[action as keyof typeof titles])
    : t(I18nKey.AUTH$INVALID_ACTION_LINK);

  const renderContent = () => {
    if (!capabilities.data)
      return <p role="alert">{t(I18nKey.AUTH$UNAVAILABLE)}</p>;
    if (capabilities.data.mode !== "local")
      return <p>{t(I18nKey.AUTH$USE_LOGIN_PROVIDER)}</p>;
    if (!isKnownAction || missingToken)
      return <p role="alert">{t(I18nKey.AUTH$INVALID_ACTION_LINK)}</p>;
    if (emailUnavailable)
      return <p role="alert">{t(I18nKey.AUTH$EMAIL_UNAVAILABLE)}</p>;
    if (forgot.isSuccess || requestVerification.isSuccess)
      return <p role="status">{t(I18nKey.AUTH$CHECK_EMAIL)}</p>;
    return (
      <form onSubmit={submit} className="flex w-full flex-col gap-4">
        {action === "change-password" && (
          <>
            <p className="text-sm">
              {t(I18nKey.AUTH$CHANGE_PASSWORD_DESCRIPTION)}
            </p>
            <AccountField
              id="current_password"
              label={t(I18nKey.AUTH$CURRENT_PASSWORD)}
              type="password"
              autoComplete="current-password"
              required
            />
          </>
        )}
        {action === "forgot-password" && (
          <AccountField
            id="email"
            label={t(I18nKey.SETTINGS$USER_EMAIL)}
            type="email"
            autoComplete="username"
            required
          />
        )}
        {isPasswordAction && (
          <>
            <AccountField
              id="new_password"
              label={t(I18nKey.AUTH$NEW_PASSWORD)}
              type="password"
              autoComplete="new-password"
              required
            />
            <p className="text-sm text-gray-400">
              {t(I18nKey.AUTH$PASSWORD_REQUIREMENTS)}
            </p>
            <AccountField
              id="confirm_password"
              label={t(I18nKey.AUTH$CONFIRM_PASSWORD)}
              type="password"
              autoComplete="new-password"
              required
            />
          </>
        )}
        {action === "verify-email" && (
          <p className="text-sm">
            {t(
              token
                ? I18nKey.AUTH$VERIFY_EMAIL_DESCRIPTION
                : I18nKey.AUTH$REQUEST_VERIFICATION_DESCRIPTION,
            )}
          </p>
        )}
        {mismatch && (
          <p role="alert" className="text-sm text-danger">
            {t(I18nKey.AUTH$PASSWORD_MISMATCH)}
          </p>
        )}
        {invalidPassword && (
          <p role="alert" className="text-sm text-danger">
            {t(I18nKey.AUTH$PASSWORD_REQUIREMENTS)}
          </p>
        )}
        <AccountError error={error} />
        <AccountSubmit isPending={isPending}>
          {action === "forgot-password" || (action === "verify-email" && !token)
            ? t(I18nKey.AUTH$SEND_EMAIL)
            : title}
        </AccountSubmit>
      </form>
    );
  };

  return (
    <main className="min-h-screen flex items-center justify-center bg-base p-4">
      <AccountCard title={title}>
        {capabilities.isLoading ? (
          <p role="status">{t(I18nKey.HOME$LOADING)}</p>
        ) : (
          <>
            {renderContent()}
            {action === "change-password" && (
              <button
                type="button"
                className="text-sm underline"
                disabled={logout.isPending}
                onClick={() => logout.mutate()}
              >
                {t(I18nKey.ACCOUNT_SETTINGS$LOGOUT)}
              </button>
            )}
            <a
              href={authPageUrl("/login", returnTo, invitationToken)}
              className="text-sm underline"
            >
              {t(I18nKey.AUTH$BACK_TO_LOGIN)}
            </a>
          </>
        )}
      </AccountCard>
    </main>
  );
}
