import React from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { usePasswordLogin } from "#/hooks/mutation/use-local-auth";
import { authPageUrl, completeAuth } from "#/utils/auth-redirect";
import { TermsAndPrivacyNotice } from "#/components/shared/terms-and-privacy-notice";
import {
  AccountCard,
  AccountField,
  AccountSubmit,
  AccountError,
} from "./account-form";

export function PasswordLogin({
  returnTo,
  invitationToken,
  emailRecovery,
}: {
  returnTo: string;
  invitationToken: string | null;
  emailRecovery: boolean;
}) {
  const { t } = useTranslation();
  const login = usePasswordLogin();
  const [passwordTooLong, setPasswordTooLong] = React.useState(false);
  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password"));
    setPasswordTooLong(Array.from(password).length > 1024);
    if (Array.from(password).length > 1024) return;
    login.mutate(
      {
        email: String(form.get("email")),
        password,
        redirect_url: returnTo,
        invitation_token: invitationToken || undefined,
      },
      { onSuccess: completeAuth },
    );
  };
  return (
    <AccountCard title={t(I18nKey.AUTH$PASSWORD_SIGN_IN)}>
      <form onSubmit={submit} className="flex w-full flex-col gap-4">
        {invitationToken && (
          <p className="text-sm">{t(I18nKey.AUTH$INVITATION_PENDING)}</p>
        )}
        <AccountField
          id="email"
          label={t(I18nKey.SETTINGS$USER_EMAIL)}
          type="email"
          autoComplete="username"
          required
        />
        <AccountField
          id="password"
          label={t(I18nKey.AUTH$PASSWORD)}
          type="password"
          autoComplete="current-password"
          required
        />
        <AccountError error={login.error} />
        {passwordTooLong && (
          <p role="alert" className="text-sm text-danger">
            {t(I18nKey.AUTH$INVALID_CREDENTIALS)}
          </p>
        )}
        <AccountSubmit isPending={login.isPending}>
          {t(I18nKey.AUTH$PASSWORD_SIGN_IN)}
        </AccountSubmit>
        {emailRecovery && (
          <a
            className="text-sm underline"
            href={authPageUrl(
              "/auth/forgot-password",
              returnTo,
              invitationToken,
            )}
          >
            {t(I18nKey.AUTH$FORGOT_PASSWORD)}
          </a>
        )}
        {invitationToken && (
          <a
            className="text-sm underline"
            href={authPageUrl("/auth/enroll", returnTo, invitationToken)}
          >
            {t(I18nKey.AUTH$ENROLL_INVITATION)}
          </a>
        )}
        <p className="text-sm text-gray-400">
          {t(I18nKey.AUTH$ENROLLMENT_POLICY)}
        </p>
      </form>
      <TermsAndPrivacyNotice />
    </AccountCard>
  );
}
