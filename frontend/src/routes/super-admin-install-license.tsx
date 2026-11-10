import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import { LicenseSettings } from "#/components/features/license/license-settings";
import { BrandButton } from "#/components/features/settings/brand-button";
import { useConfiguredLicense } from "#/hooks/query/use-configured-license";
import { I18nKey } from "#/i18n/declaration";
import { markSuperAdminNuxLicenseDone } from "#/utils/org/super-admin-nux";

/**
 * The license is optional here: the Super Admin can skip this step and add
 * the license later on the Super Admin License page.
 */
export default function SuperAdminInstallLicense() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data: license } = useConfiguredLicense();
  const hasLicense = Boolean(license?.kind);

  const onNext = () => {
    markSuperAdminNuxLicenseDone();
    navigate("/install/company");
  };

  return (
    <div
      className="flex w-full max-w-lg flex-col items-center gap-6"
      data-testid="super-admin-install-license"
    >
      <OpenHandsLogoWhite
        width={68}
        height={46}
        aria-label={t(I18nKey.BRANDING$OPENHANDS_LOGO)}
      />
      <div className="flex flex-col gap-2 text-center">
        <h1 className="text-2xl font-semibold text-white">
          {t(I18nKey.SA_NUX$LICENSE_TITLE)}
        </h1>
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SA_NUX$LICENSE_BODY)}
        </p>
      </div>

      <div className="w-full">
        <LicenseSettings onSaved={onNext} />
      </div>

      <BrandButton
        type="button"
        variant={hasLicense ? "primary" : "secondary"}
        className="w-full"
        testId="sa-nux-license-continue"
        onClick={onNext}
      >
        {t(hasLicense ? I18nKey.SA_NUX$NEXT : I18nKey.SA_NUX$LICENSE_SKIP)}
      </BrandButton>
    </div>
  );
}
