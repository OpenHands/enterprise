import React from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy } from "lucide-react";
import { useServerInfo } from "#/hooks/query/use-server-info";
import { useLicenseStatus } from "#/hooks/query/use-license-status";
import { useConfig } from "#/hooks/query/use-config";
import { LicenseState } from "#/api/license-service/license-service.api";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import { cn } from "#/utils/utils";
import { displayErrorToast } from "#/utils/custom-toast-handlers";

// Maps the existing LicenseStatus.state values (also used by LicenseBanner)
// to a short, human label. Deliberately excludes any identifier (key hint,
// customer_center_url) — only the normalized status is surfaced here.
const LICENSE_STATE_LABEL_KEYS: Record<LicenseState, I18nKey> = {
  valid: I18nKey.SETTINGS$ABOUT_LICENSE_STATE_VALID,
  invalid: I18nKey.SETTINGS$ABOUT_LICENSE_STATE_INVALID,
  pending: I18nKey.SETTINGS$ABOUT_LICENSE_STATE_PENDING,
  unverified: I18nKey.SETTINGS$ABOUT_LICENSE_STATE_UNVERIFIED,
  missing_key: I18nKey.SETTINGS$ABOUT_LICENSE_STATE_MISSING_KEY,
  disabled: I18nKey.SETTINGS$ABOUT_LICENSE_STATE_DISABLED,
};

function VersionRow({
  label,
  value,
  testId,
  emphasized,
}: {
  label: string;
  value: string | undefined;
  testId: string;
  emphasized?: boolean;
}) {
  const { t } = useTranslation();

  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <Typography.Text
        className={cn(
          "text-xs text-gray-400",
          emphasized && "font-medium text-white",
        )}
      >
        {label}
      </Typography.Text>
      <span
        className={cn(
          "font-mono text-xs text-white",
          emphasized && "text-sm font-semibold",
        )}
        data-testid={testId}
      >
        {value || t(I18nKey.SETTINGS$ABOUT_VALUE_UNAVAILABLE)}
      </span>
    </div>
  );
}

/**
 * Read-only "About" card: surfaces the deployed OpenHands Enterprise release
 * (when packaging provides it) and the component versions bundled into the
 * server, so support/users can discover them without reading backend logs
 * or the Helm chart.
 *
 * Only allowlisted, non-sensitive fields from ``GET /server_info`` are
 * rendered or copied (enterprise_release, app_version, sdk_version) — never
 * host/URL, username, org id, or credential data. See
 * ``option.types.ts#ServerInfo``.
 */
export function AboutSection() {
  const { t } = useTranslation();
  const { data, isLoading, isError } = useServerInfo();
  const { data: config } = useConfig();
  // Reuses the same hook, gating, and state semantics as LicenseBanner:
  // the license check only runs in SaaS/self-hosted-enterprise mode, never
  // on the pure OSS server (see server/routes license-banner.tsx).
  const {
    data: licenseStatus,
    isLoading: isLicenseLoading,
    isError: isLicenseError,
  } = useLicenseStatus({ enabled: config?.app_mode === "saas" });
  const [copied, setCopied] = React.useState(false);

  const enterpriseRelease = data?.enterprise_release || undefined;
  const serverVersion = data?.app_version;
  const sdkVersion = data?.sdk_version;
  const hasAnyVersion = Boolean(
    enterpriseRelease || serverVersion || sdkVersion,
  );

  const showLicenseSection = config?.app_mode === "saas";
  const licenseStateLabel = licenseStatus
    ? t(LICENSE_STATE_LABEL_KEYS[licenseStatus.state])
    : undefined;
  // `type` is the license type (trial/paid/development); shown as-is, never
  // the license key or customer_center_url.
  const licenseType = licenseStatus?.type || undefined;

  const handleCopy = async () => {
    const unavailable = t(I18nKey.SETTINGS$ABOUT_VALUE_UNAVAILABLE);
    const lines = [
      `OpenHands Enterprise release: ${enterpriseRelease || unavailable}`,
      `App server component version: ${serverVersion || unavailable}`,
      `Agent SDK component version: ${sdkVersion || unavailable}`,
    ];
    if (showLicenseSection) {
      lines.push(
        `License status: ${licenseStateLabel || unavailable}`,
        `License type: ${licenseType || unavailable}`,
      );
    }
    try {
      if (!navigator.clipboard) {
        throw new Error("Clipboard API unavailable");
      }
      await navigator.clipboard.writeText(lines.join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      displayErrorToast(t(I18nKey.SETTINGS$ABOUT_COPY_FAILED));
    }
  };

  return (
    <div
      data-testid="about-section"
      className="flex flex-col gap-2 border-t border-tertiary pt-6"
    >
      <Typography variant="h3">{t(I18nKey.SETTINGS$ABOUT_TITLE)}</Typography>

      {isLoading && (
        <Typography.Text
          testId="about-section-loading"
          className="text-xs text-gray-400"
        >
          {t(I18nKey.SETTINGS$ABOUT_LOADING)}
        </Typography.Text>
      )}

      {!isLoading && isError && (
        <Typography.Text
          testId="about-section-error"
          className="text-xs text-gray-400"
        >
          {t(I18nKey.SETTINGS$ABOUT_UNAVAILABLE)}
        </Typography.Text>
      )}

      {!isLoading && !isError && (
        <div className="flex flex-col gap-3">
          {/* Primary is the installed chart release, or the verified bundle
              release supplied by another installation method. */}
          <VersionRow
            label={t(I18nKey.SETTINGS$ABOUT_ENTERPRISE_RELEASE)}
            value={enterpriseRelease}
            testId="about-section-enterprise-release"
            emphasized
          />
          <div>
            <Typography.Text className="text-[11px] uppercase tracking-wide text-gray-400">
              {t(I18nKey.SETTINGS$ABOUT_COMPONENT_VERSIONS_TITLE)}
            </Typography.Text>
            <div className="flex flex-col divide-y divide-tertiary">
              <VersionRow
                label={t(I18nKey.SETTINGS$ABOUT_SERVER_VERSION)}
                value={serverVersion}
                testId="about-section-server-version"
              />
              <VersionRow
                label={t(I18nKey.SETTINGS$ABOUT_SDK_VERSION)}
                value={sdkVersion}
                testId="about-section-sdk-version"
              />
            </div>
          </div>

          {showLicenseSection && (
            <div>
              <Typography.Text className="text-[11px] uppercase tracking-wide text-gray-400">
                {t(I18nKey.SETTINGS$ABOUT_LICENSE_TITLE)}
              </Typography.Text>
              {isLicenseLoading && (
                <Typography.Text
                  testId="about-section-license-loading"
                  className="text-xs text-gray-400"
                >
                  {t(I18nKey.SETTINGS$ABOUT_LOADING)}
                </Typography.Text>
              )}
              {!isLicenseLoading && isLicenseError && (
                <Typography.Text
                  testId="about-section-license-error"
                  className="text-xs text-gray-400"
                >
                  {t(I18nKey.SETTINGS$ABOUT_UNAVAILABLE)}
                </Typography.Text>
              )}
              {!isLicenseLoading && !isLicenseError && (
                <div className="flex flex-col divide-y divide-tertiary">
                  <VersionRow
                    label={t(I18nKey.SETTINGS$ABOUT_LICENSE_STATUS)}
                    value={licenseStateLabel}
                    testId="about-section-license-status"
                  />
                  <VersionRow
                    label={t(I18nKey.SETTINGS$ABOUT_LICENSE_TYPE)}
                    value={licenseType}
                    testId="about-section-license-type"
                  />
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {!isLoading && !isError && hasAnyVersion && (
        <button
          type="button"
          data-testid="about-section-copy-button"
          onClick={handleCopy}
          className={cn(
            "flex w-fit items-center gap-1 text-xs cursor-pointer hover:underline",
            copied ? "text-success" : "text-primary",
          )}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {t(
            copied
              ? I18nKey.SETTINGS$ABOUT_COPIED
              : I18nKey.SETTINGS$ABOUT_COPY_BUTTON,
          )}
        </button>
      )}
    </div>
  );
}
