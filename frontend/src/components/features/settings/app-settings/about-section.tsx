import React from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy } from "lucide-react";
import { useReleaseInfo } from "#/hooks/query/use-release-info";
import { useLicenseStatus } from "#/hooks/query/use-license-status";
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

// The backend's license `type` is a free-form string (see
// server/licensing/verification.py: "trial, paid or development"), not a
// closed enum, so only these three documented values are localized. Any
// other value (future type, or an unexpected one, including a value like
// "constructor" or "__proto__") falls back to the raw string rather than
// silently hiding it. A Map (not a plain object) avoids any prototype-chain
// lookup surprises for such values.
const LICENSE_TYPE_LABEL_KEYS = new Map<string, I18nKey>([
  ["trial", I18nKey.SETTINGS$ABOUT_LICENSE_TYPE_TRIAL],
  ["paid", I18nKey.SETTINGS$ABOUT_LICENSE_TYPE_PAID],
  ["development", I18nKey.SETTINGS$ABOUT_LICENSE_TYPE_DEVELOPMENT],
]);

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
 * Only allowlisted, non-sensitive fields from ``GET /api/v1/release`` are
 * rendered or copied (enterprise_release, app_version, sdk_version) — never
 * host/URL, username, org id, or credential data. See
 * ``option.types.ts#ReleaseInfo``.
 */
export function AboutSection({
  isEnterpriseSelfHosted,
}: {
  /** Mirrors the same flag app-settings.tsx derives from useConfig():
   *  `config?.app_mode === "saas" && config?.feature_flags?.deployment_mode
   *  === "self_hosted"`. Passed in rather than re-derived here so this
   *  component doesn't need its own `useConfig()` call, and the Enterprise
   *  release/license rows — which are meaningless on the pure OSS server and
   *  on cloud SaaS (app.all-hands.dev) — only ever render for self-hosted
   *  Enterprise installs. */
  isEnterpriseSelfHosted: boolean;
}) {
  const { t } = useTranslation();
  const { data, isLoading, isError } = useReleaseInfo();
  // Reuses the same hook, gating, and state semantics as LicenseBanner:
  // the license check only runs for self-hosted Enterprise installs, never
  // on the pure OSS server or on cloud SaaS (see license-banner.tsx).
  const {
    data: licenseStatus,
    isLoading: isLicenseLoading,
    isError: isLicenseError,
  } = useLicenseStatus({ enabled: isEnterpriseSelfHosted });
  const [copied, setCopied] = React.useState(false);
  const copiedTimer = React.useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const mounted = React.useRef(false);

  React.useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      clearTimeout(copiedTimer.current);
    };
  }, []);

  // Only self-hosted Enterprise installs have a customer-facing release
  // number to show; cloud SaaS and the pure OSS server never set
  // OPENHANDS_ENTERPRISE_RELEASE, so showing an "Unavailable" row there
  // would be noise rather than a real gap in packaging.
  const enterpriseRelease = isEnterpriseSelfHosted
    ? data?.enterprise_release || undefined
    : undefined;
  const serverVersion = data?.app_version;
  const sdkVersion = data?.sdk_version;
  const hasAnyVersion = Boolean(
    enterpriseRelease || serverVersion || sdkVersion,
  );

  const showLicenseSection = isEnterpriseSelfHosted;
  const licenseStateLabel = licenseStatus
    ? t(LICENSE_STATE_LABEL_KEYS[licenseStatus.state])
    : undefined;
  // `type` is the license type (trial/paid/development); localized when it
  // matches a documented value, otherwise shown as the raw string rather
  // than hidden. Never the license key or customer_center_url.
  const rawLicenseType = licenseStatus?.type || undefined;
  const licenseTypeLabelKey = rawLicenseType
    ? LICENSE_TYPE_LABEL_KEYS.get(rawLicenseType)
    : undefined;
  const licenseType = licenseTypeLabelKey
    ? t(licenseTypeLabelKey)
    : rawLicenseType;

  const handleCopy = async () => {
    const unavailable = "unavailable";
    const lines = [
      ...(isEnterpriseSelfHosted
        ? [`OpenHands Enterprise release: ${enterpriseRelease || unavailable}`]
        : []),
      `App server component version: ${serverVersion || unavailable}`,
      `Agent SDK component version: ${sdkVersion || unavailable}`,
    ];
    if (showLicenseSection) {
      lines.push(
        `License status: ${licenseStatus?.state || unavailable}`,
        `License type: ${rawLicenseType || unavailable}`,
      );
    }
    try {
      if (!navigator.clipboard) {
        throw new Error("Clipboard API unavailable");
      }
      await navigator.clipboard.writeText(lines.join("\n"));
      if (!mounted.current) return;
      clearTimeout(copiedTimer.current);
      setCopied(true);
      copiedTimer.current = setTimeout(() => setCopied(false), 2000);
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
          {/* The customer-facing release (e.g. Enterprise release notes
              number) only applies to self-hosted Enterprise installs — it's
              never set on cloud SaaS or the pure OSS server, so the row
              itself is hidden rather than shown as "Unavailable" there. */}
          {isEnterpriseSelfHosted && (
            <VersionRow
              label={t(I18nKey.SETTINGS$ABOUT_ENTERPRISE_RELEASE)}
              value={enterpriseRelease}
              testId="about-section-enterprise-release"
              emphasized
            />
          )}
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
