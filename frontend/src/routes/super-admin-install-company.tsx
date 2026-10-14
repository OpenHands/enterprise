import React from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { ImagePlus } from "lucide-react";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { I18nKey } from "#/i18n/declaration";
import { useInstanceSettings } from "#/hooks/query/use-super-admin";
import { useUpdateInstanceSettings } from "#/hooks/mutation/use-super-admin-mutations";
import { readImageFileAsDataUrl } from "#/utils/org/instance-logo";
import { markSuperAdminNuxCompanyDone } from "#/utils/org/super-admin-nux";

export default function SuperAdminInstallCompany() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [companyName, setCompanyName] = React.useState("");
  const [logoUrl, setLogoUrl] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const logoInputRef = React.useRef<HTMLInputElement>(null);
  const { data: instanceSettings } = useInstanceSettings();
  const { mutate: updateInstanceSettings, isPending } =
    useUpdateInstanceSettings();
  const shownLogo = logoUrl ?? instanceSettings?.logo ?? null;

  const canContinue = companyName.trim().length > 1;

  const onLogo = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const input = event.target;
    const file = input.files?.[0];
    input.value = "";
    if (!file?.type.startsWith("image/")) {
      return;
    }
    try {
      const dataUrl = await readImageFileAsDataUrl(file);
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
    updateInstanceSettings(
      {
        company_name: companyName.trim(),
        ...(logoUrl ? { logo: logoUrl } : {}),
      },
      {
        onSuccess: () => {
          markSuperAdminNuxCompanyDone({
            name: companyName,
            hasLogo: shownLogo != null,
          });
          navigate("/install/org");
        },
      },
    );
  };

  return (
    <form
      className="flex w-full max-w-lg flex-col items-center gap-6"
      data-testid="super-admin-install-company"
      onSubmit={onContinue}
    >
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
            {shownLogo ? (
              <img src={shownLogo} alt="" className="size-full object-cover" />
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
        isDisabled={!canContinue || isPending}
      >
        {t(I18nKey.SA_NUX$NEXT)}
      </BrandButton>
    </form>
  );
}
