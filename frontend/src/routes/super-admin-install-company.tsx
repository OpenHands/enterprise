import React from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { ImagePlus } from "lucide-react";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { I18nKey } from "#/i18n/declaration";
import {
  readImageFileAsDataUrl,
  readInstanceLogo,
  setInstanceLogo,
} from "#/utils/org/instance-logo";
import { markSuperAdminNuxCompanyDone } from "#/utils/org/super-admin-nux";
import { cn } from "#/utils/utils";

/** Where a new install requests a license key. Opens in a new tab. */
const LICENSE_REGISTER_URL = "https://www.openhands.dev/contact";

/** Existing customers sign in to the installer dashboard. Opens in a new tab. */
const CUSTOMER_CENTER_LOGIN_URL =
  "https://install.r9.all-hands.dev/openhands/login";

export default function SuperAdminInstallCompany() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [companyName, setCompanyName] = React.useState("");
  const [licenseKey, setLicenseKey] = React.useState("");
  const [connectedKey, setConnectedKey] = React.useState<string | null>(null);
  const [logoUrl, setLogoUrl] = React.useState<string | null>(() =>
    readInstanceLogo(),
  );
  const [error, setError] = React.useState<string | null>(null);
  const logoInputRef = React.useRef<HTMLInputElement>(null);

  const trimmedKey = licenseKey.trim();
  const hasKey = connectedKey != null && connectedKey === trimmedKey;
  const canContinue = companyName.trim().length > 1;

  const onRegister = () => {
    window.open(LICENSE_REGISTER_URL, "_blank", "noopener,noreferrer");
  };

  const onLogin = () => {
    window.open(CUSTOMER_CENTER_LOGIN_URL, "_blank", "noopener,noreferrer");
  };

  const onConnect = () => {
    if (!trimmedKey) {
      return;
    }
    setConnectedKey(trimmedKey);
  };

  const onLogo = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !file.type.startsWith("image/")) {
      return;
    }
    try {
      const dataUrl = await readImageFileAsDataUrl(file);
      setInstanceLogo(dataUrl);
      setLogoUrl(dataUrl);
      setError(null);
    } catch {
      setError(t(I18nKey.SUPER_ADMIN$INSTANCE_LOGO_ERROR));
    }
  };

  const onContinue = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canContinue) {
      setError(t(I18nKey.SA_NUX$COMPANY_INVALID));
      return;
    }
    setError(null);
    // Frontend NUX only — the key is not stored. A later bootstrap API redeems it.
    markSuperAdminNuxCompanyDone({
      name: companyName,
      hasLicenseKey: hasKey,
      hasLogo: logoUrl != null,
    });
    navigate("/install/org");
  };

  return (
    <form
      className="flex w-full max-w-lg flex-col items-center gap-6"
      data-testid="super-admin-install-company"
      onSubmit={onContinue}
    >
      <div className="flex w-full justify-end">
        <BrandButton
          type="button"
          variant="secondary"
          testId="sa-nux-customer-login"
          onClick={onLogin}
        >
          {t(I18nKey.SA_NUX$LOGIN)}
        </BrandButton>
      </div>
      <OpenHandsLogoWhite
        width={68}
        height={46}
        aria-label={t(I18nKey.BRANDING$OPENHANDS_LOGO)}
      />
      <div className="flex flex-col gap-2 text-center">
        <h1 className="text-2xl font-semibold text-white">
          {t(I18nKey.SA_NUX$COMPANY_TITLE)}
        </h1>
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SA_NUX$COMPANY_BODY)}
        </p>
      </div>

      <div className="flex w-full items-start gap-4">
        <div className="flex shrink-0 flex-col gap-2.5">
          <span className="text-sm" id="sa-nux-company-logo-label">
            {t(I18nKey.SA_NUX$COMPANY_LOGO)}
          </span>
          <button
            type="button"
            className="flex size-20 items-center justify-center overflow-hidden rounded-xl border border-[var(--oh-border)] bg-base-secondary text-[var(--oh-muted)] hover:bg-surface-raised"
            data-testid="sa-nux-company-logo"
            aria-labelledby="sa-nux-company-logo-label"
            onClick={() => logoInputRef.current?.click()}
          >
            {logoUrl ? (
              <img src={logoUrl} alt="" className="size-full object-cover" />
            ) : (
              <ImagePlus className="size-5" strokeWidth={1.75} aria-hidden />
            )}
          </button>
          <input
            ref={logoInputRef}
            type="file"
            accept="image/*"
            className="sr-only"
            tabIndex={-1}
            data-testid="sa-nux-company-logo-input"
            onChange={onLogo}
          />
        </div>
        <div className="min-w-0 flex-1">
          <SettingsInput
            testId="sa-nux-company-name"
            name="companyName"
            label={t(I18nKey.SA_NUX$COMPANY_NAME_LABEL)}
            type="text"
            value={companyName}
            onChange={setCompanyName}
            placeholder={t(I18nKey.SA_NUX$COMPANY_NAME_PLACEHOLDER)}
          />
        </div>
      </div>

      <section
        className="flex w-full flex-col gap-3 rounded-lg border border-[var(--oh-border)] bg-base-secondary p-4 text-left"
        data-testid="sa-nux-plans"
        aria-labelledby="sa-nux-plans-title"
      >
        <h2
          id="sa-nux-plans-title"
          className="text-sm font-semibold text-white"
        >
          {t(I18nKey.SA_NUX$PLANS_TITLE)}
        </h2>
        <div className="flex items-end gap-2">
          <SettingsInput
            testId="sa-nux-license-key"
            name="licenseKey"
            label={t(I18nKey.SA_NUX$LICENSE_KEY_LABEL)}
            type="text"
            className="min-w-0 flex-1"
            value={licenseKey}
            onChange={setLicenseKey}
            placeholder={t(I18nKey.SA_NUX$LICENSE_KEY_PLACEHOLDER)}
            autoComplete="off"
          />
          <BrandButton
            type="button"
            variant="primary"
            className="shrink-0"
            testId="sa-nux-connect-key"
            isDisabled={!trimmedKey}
            onClick={onConnect}
          >
            {t(I18nKey.SETTINGS$CONNECT)}
          </BrandButton>
        </div>
        <div className="flex flex-col gap-2">
          <p
            className={cn(
              "rounded-md border px-3 py-2 text-sm leading-5",
              hasKey
                ? "border-[var(--oh-border)] text-[var(--oh-muted)]"
                : "border-white text-white",
            )}
            data-testid="sa-nux-trial-without-key"
          >
            {t(I18nKey.SA_NUX$TRIAL_WITHOUT_KEY)}
          </p>
          <div
            className={cn(
              "flex flex-col gap-3 rounded-md border px-3 py-3",
              hasKey
                ? "border-white text-white"
                : "border-[var(--oh-border)] text-[var(--oh-muted)]",
            )}
            data-testid="sa-nux-trial-with-key"
          >
            <p className="text-sm leading-5">{t(I18nKey.SA_NUX$TRIAL_WITH_KEY)}</p>
            <BrandButton
              type="button"
              variant="secondary"
              className="w-full"
              testId="sa-nux-register-key"
              onClick={onRegister}
            >
              {t(I18nKey.SA_NUX$REGISTER_KEY)}
            </BrandButton>
          </div>
        </div>
      </section>

      {error && (
        <p className="w-full text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <BrandButton
        type="submit"
        variant="primary"
        className="w-full"
        testId="sa-nux-company-continue"
        isDisabled={!canContinue}
      >
        {t(I18nKey.SA_NUX$NEXT)}
      </BrandButton>
    </form>
  );
}
