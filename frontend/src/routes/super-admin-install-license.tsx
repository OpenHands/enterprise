import { useNavigate } from "react-router";
import { Trans, useTranslation } from "react-i18next";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import { CustomerCenterLink } from "#/components/features/license/customer-center-link";
import { LicenseSettings } from "#/components/features/license/license-settings";
import { BrandButton } from "#/components/features/settings/brand-button";
import { useConfig } from "#/hooks/query/use-config";
import { useConfiguredLicense } from "#/hooks/query/use-configured-license";
import { useLicenseStatus } from "#/hooks/query/use-license-status";
import { I18nKey } from "#/i18n/declaration";
import { markSuperAdminNuxLicenseDone } from "#/utils/org/super-admin-nux";

/** Licensing can be configured later, so this step must remain skippable. */
export default function SuperAdminInstallLicense() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data: config } = useConfig();
  const { data: license } = useConfiguredLicense();
  const checksStatus = config?.app_mode === "saas";
  const { data: status } = useLicenseStatus({ enabled: checksStatus });
  // Until a saved license's status loads, its validity is unknown.
  const statusPending = Boolean(license?.kind) && checksStatus && !status;
  const hasUsableLicense =
    Boolean(license?.kind) && !statusPending && status?.state !== "invalid";

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
          <Trans
            i18nKey={I18nKey.SA_NUX$LICENSE_BODY}
            components={[<CustomerCenterLink key="customer-center" />]}
          />
        </p>
      </div>

      <div className="w-full">
        <LicenseSettings onSaved={onNext} />
      </div>

      <BrandButton
        type="button"
        variant={hasUsableLicense ? "primary" : "secondary"}
        className="w-full"
        testId="sa-nux-license-continue"
        isDisabled={statusPending}
        onClick={onNext}
      >
        {t(
          hasUsableLicense ? I18nKey.SA_NUX$NEXT : I18nKey.SA_NUX$LICENSE_SKIP,
        )}
      </BrandButton>
    </div>
  );
}
