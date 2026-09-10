import React from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useSettings } from "#/hooks/query/use-settings";
import {
  useChangeLoginEmail,
  useRequestEmailVerification,
} from "#/hooks/mutation/use-local-auth";
import { useInvitation } from "#/hooks/use-invitation";
import { AccountError, AccountField, AccountSubmit } from "./account-form";

export function LocalAccountSettings({
  emailRecovery,
}: {
  emailRecovery: boolean;
}) {
  const { t } = useTranslation();
  const { data: settings, isLoading } = useSettings();
  const { invitationToken } = useInvitation();
  const changeEmail = useChangeLoginEmail();
  const verification = useRequestEmailVerification();
  const context = {
    redirect_url: "/settings/user",
    invitation_token: invitationToken || undefined,
  };
  const submitEmail = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const email = String(new FormData(event.currentTarget).get("email"));
    changeEmail.mutate({ ...context, email });
  };
  return (
    <div
      data-testid="local-account-settings"
      className="flex max-w-lg flex-col gap-6"
    >
      {isLoading ? (
        <p role="status">{t(I18nKey.HOME$LOADING)}</p>
      ) : (
        <>
          <div>
            <h3 className="text-lg font-medium">
              {t(I18nKey.SETTINGS$USER_EMAIL)}
            </h3>
            <p>{settings?.email}</p>
            {settings?.email_verified === false && (
              <p className="mt-2 text-sm text-gray-400">
                {t(I18nKey.AUTH$EMAIL_NOT_VERIFIED)}
              </p>
            )}
          </div>
          {emailRecovery && (
            <>
              {settings?.email_verified === false && (
                <button
                  type="button"
                  className="self-start underline"
                  disabled={verification.isPending}
                  onClick={() => verification.mutate(context)}
                >
                  {t(I18nKey.SETTINGS$RESEND_VERIFICATION)}
                </button>
              )}
              <form onSubmit={submitEmail} className="flex flex-col gap-4">
                <AccountField
                  id="email"
                  label={t(I18nKey.AUTH$NEW_LOGIN_EMAIL)}
                  type="email"
                  autoComplete="email"
                  required
                />
                <p className="text-sm text-gray-400">
                  {t(I18nKey.AUTH$EMAIL_CHANGE_DESCRIPTION)}
                </p>
                <AccountSubmit isPending={changeEmail.isPending}>
                  {t(I18nKey.AUTH$SEND_EMAIL)}
                </AccountSubmit>
              </form>
            </>
          )}
          {(verification.isSuccess || changeEmail.isSuccess) && (
            <p role="status">{t(I18nKey.AUTH$CHECK_EMAIL)}</p>
          )}
          <AccountError error={verification.error || changeEmail.error} />
          <a
            className="self-start underline"
            href="/auth/change-password?returnTo=%2Fsettings%2Fuser"
          >
            {t(I18nKey.AUTH$CHANGE_PASSWORD)}
          </a>
        </>
      )}
    </div>
  );
}
