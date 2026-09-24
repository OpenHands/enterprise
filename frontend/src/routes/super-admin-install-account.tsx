import React from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { I18nKey } from "#/i18n/declaration";
import { markSuperAdminNuxAccountDone } from "#/utils/org/super-admin-nux";

export default function SuperAdminInstallAccount() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [name, setName] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  const canSubmit =
    name.trim().length > 1 &&
    email.includes("@") &&
    password.length >= 8 &&
    password === confirm;

  const onCreate = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) {
      setError(
        password !== confirm
          ? t(I18nKey.SA_NUX$PASSWORD_MISMATCH)
          : t(I18nKey.SA_NUX$ACCOUNT_INVALID),
      );
      return;
    }
    setError(null);
    // Frontend NUX only — persists profile locally until Keycloak/bootstrap API exists.
    markSuperAdminNuxAccountDone({ name, email });
    navigate("/super-admin/setup");
  };

  return (
    <form
      className="flex w-full max-w-md flex-col items-center gap-6"
      data-testid="super-admin-install-account"
      onSubmit={onCreate}
    >
      <OpenHandsLogoWhite
        width={68}
        height={46}
        aria-label={t(I18nKey.BRANDING$OPENHANDS_LOGO)}
      />
      <div className="flex flex-col gap-2 text-center">
        <h1 className="text-2xl font-semibold text-white">
          {t(I18nKey.SA_NUX$ACCOUNT_TITLE)}
        </h1>
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SA_NUX$ACCOUNT_BODY)}
        </p>
      </div>

      <div className="flex w-full flex-col gap-3">
        <SettingsInput
          testId="sa-nux-name"
          name="name"
          label={t(I18nKey.SA_NUX$NAME_LABEL)}
          type="text"
          value={name}
          onChange={setName}
          placeholder={t(I18nKey.SA_NUX$NAME_PLACEHOLDER)}
        />
        <SettingsInput
          testId="sa-nux-email"
          name="email"
          label={t(I18nKey.SA_NUX$EMAIL_LABEL)}
          type="email"
          value={email}
          onChange={setEmail}
          placeholder={t(I18nKey.SA_NUX$EMAIL_PLACEHOLDER)}
        />
        <SettingsInput
          testId="sa-nux-password"
          name="password"
          label={t(I18nKey.SA_NUX$PASSWORD_LABEL)}
          type="password"
          value={password}
          onChange={setPassword}
          placeholder={t(I18nKey.SA_NUX$PASSWORD_PLACEHOLDER)}
        />
        <SettingsInput
          testId="sa-nux-password-confirm"
          name="passwordConfirm"
          label={t(I18nKey.SA_NUX$PASSWORD_CONFIRM_LABEL)}
          type="password"
          value={confirm}
          onChange={setConfirm}
          placeholder={t(I18nKey.SA_NUX$PASSWORD_CONFIRM_PLACEHOLDER)}
        />
      </div>

      {error && (
        <p className="w-full text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <BrandButton
        type="submit"
        variant="primary"
        className="w-full"
        testId="sa-nux-account-continue"
        isDisabled={!canSubmit}
      >
        {t(I18nKey.SA_NUX$CREATE_ACCOUNT)}
      </BrandButton>
    </form>
  );
}
