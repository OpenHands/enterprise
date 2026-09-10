import React from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useMe } from "#/hooks/query/use-me";
import { useAuthCapabilities } from "#/hooks/query/use-auth-capabilities";
import { useCreateAccount } from "#/hooks/mutation/use-local-auth";
import {
  AccountField,
  AccountSubmit,
  AccountError,
} from "#/components/features/auth/account-form";
import { isValidNewPassword } from "#/utils/password-policy";

export default function ManageAccountsPage() {
  const { t } = useTranslation();
  const { data: user, isLoading: userLoading } = useMe();
  const { data: capabilities, isLoading } = useAuthCapabilities();
  const createAccount = useCreateAccount();
  const [invalidPassword, setInvalidPassword] = React.useState(false);
  const formRef = React.useRef<HTMLFormElement>(null);

  if (isLoading || userLoading)
    return <p role="status">{t(I18nKey.HOME$LOADING)}</p>;
  if (
    capabilities?.mode !== "local" ||
    !user?.permissions?.includes("manage_users")
  )
    return <p role="alert">{t(I18nKey.AUTH$ACCOUNT_ACCESS_DENIED)}</p>;

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("initial_password"));
    setInvalidPassword(!isValidNewPassword(password));
    if (!isValidNewPassword(password)) return;
    createAccount.mutate(
      { email: String(form.get("email")), initial_password: password },
      {
        onSuccess: () => formRef.current?.reset(),
      },
    );
  };

  return (
    <div className="max-w-lg flex flex-col gap-6">
      <h2 className="text-xl font-semibold">
        {t(I18nKey.AUTH$CREATE_ACCOUNT)}
      </h2>
      <p className="text-sm text-gray-400">
        {t(I18nKey.AUTH$CREATE_ACCOUNT_DESCRIPTION)}
      </p>
      <form ref={formRef} onSubmit={submit} className="flex flex-col gap-4">
        <AccountField
          id="email"
          label={t(I18nKey.SETTINGS$USER_EMAIL)}
          type="email"
          autoComplete="off"
          required
        />
        <AccountField
          id="initial_password"
          label={t(I18nKey.AUTH$INITIAL_PASSWORD)}
          type="password"
          autoComplete="new-password"
          required
        />
        <p className="text-sm text-gray-400">
          {t(I18nKey.AUTH$PASSWORD_REQUIREMENTS)}
        </p>
        {invalidPassword && (
          <p role="alert" className="text-sm text-danger">
            {t(I18nKey.AUTH$PASSWORD_REQUIREMENTS)}
          </p>
        )}
        <AccountError error={createAccount.error} />
        <AccountSubmit isPending={createAccount.isPending}>
          {t(I18nKey.AUTH$CREATE_ACCOUNT)}
        </AccountSubmit>
      </form>
      {createAccount.isSuccess && (
        <p role="status">
          {t(I18nKey.AUTH$ACCOUNT_CREATED, { email: createAccount.data.email })}
        </p>
      )}
    </div>
  );
}
