import { useTranslation } from "react-i18next";
import { FaTriangleExclamation } from "react-icons/fa6";
import { I18nKey } from "#/i18n/declaration";
import InfoCircleIcon from "#/icons/info-circle.svg?react";
import { LicenseStatus } from "#/api/license-service/license-service.api";
import { useLicenseStatus } from "#/hooks/query/use-license-status";
import { useConfig } from "#/hooks/query/use-config";
import { cn } from "#/utils/utils";

type LicenseBannerTone = "info" | "warning" | "error";

export interface LicenseBannerMessage {
  tone: LicenseBannerTone;
  key: I18nKey;
  // An ISO timestamp to show as a date in the message.
  date?: string;
}

// Admins see a warning this many days before an offline license ends. An
// online license renews by itself, so only a trial shows its end date.
const OFFLINE_LICENSE_WARNING_DAYS = 7;
const DAY_MS = 24 * 60 * 60 * 1000;

/** The banner to show for a license status, or null when nothing needs saying. */
export function getLicenseBannerMessage(
  status: LicenseStatus | undefined,
  now: Date = new Date(),
): LicenseBannerMessage | null {
  if (!status) return null;
  switch (status.state) {
    case "missing_key":
      return { tone: "error", key: I18nKey.LICENSE$MISSING_KEY };
    case "invalid":
      if (status.reason === "expired") {
        return status.license_ends_at
          ? {
              tone: "error",
              key: I18nKey.LICENSE$EXPIRED_ON,
              date: status.license_ends_at,
            }
          : { tone: "error", key: I18nKey.LICENSE$EXPIRED };
      }
      if (status.reason === "invalid_license_file") {
        return { tone: "error", key: I18nKey.LICENSE$INVALID_FILE };
      }
      return { tone: "error", key: I18nKey.LICENSE$INVALID_KEY };
    case "unverified":
      return { tone: "warning", key: I18nKey.LICENSE$UNVERIFIED };
    case "valid":
      if (
        status.offline &&
        status.license_ends_at &&
        new Date(status.license_ends_at).getTime() - now.getTime() <=
          OFFLINE_LICENSE_WARNING_DAYS * DAY_MS
      ) {
        return {
          tone: "warning",
          key: I18nKey.LICENSE$OFFLINE_ENDS_SOON,
          date: status.license_ends_at,
        };
      }
      if (status.type === "trial" && status.license_ends_at) {
        return {
          tone: "info",
          key: I18nKey.LICENSE$TRIAL_ENDS,
          date: status.license_ends_at,
        };
      }
      return null;
    default:
      return null;
  }
}

const TONE_CLASSES: Record<LicenseBannerTone, string> = {
  info: "bg-base-secondary text-[var(--oh-muted)] border-[var(--oh-border)]",
  warning: "bg-amber-500/15 text-amber-200 border-amber-500/40",
  error:
    "bg-[color-mix(in_srgb,var(--oh-danger)_18%,transparent)] text-[var(--oh-danger)] border-[color-mix(in_srgb,var(--oh-danger)_45%,transparent)]",
};

export function formatLicenseDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * A full-width strip that reports license problems, a trial's end date, and an
 * offline license that ends soon. It can't be dismissed. It renders nothing
 * when license enforcement is off, or when there is nothing to report.
 */
export function LicenseBanner() {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const { data: status } = useLicenseStatus({
    enabled: config?.app_mode === "saas",
  });

  const message = getLicenseBannerMessage(status);
  if (!message || !status) return null;

  const Icon = message.tone === "info" ? InfoCircleIcon : FaTriangleExclamation;

  return (
    <div
      data-testid="license-banner"
      data-tone={message.tone}
      role={message.tone === "info" ? "status" : "alert"}
      className={cn(
        "flex w-full shrink-0 items-center justify-center gap-2 border-b px-4 py-2 text-xs font-medium leading-5",
        TONE_CLASSES[message.tone],
      )}
    >
      <Icon width={12} height={12} className="shrink-0" />
      <span>
        {t(message.key, {
          date: message.date ? formatLicenseDate(message.date) : "",
        })}
      </span>
      <a
        href={status.customer_center_url}
        target="_blank"
        rel="noreferrer"
        className="underline underline-offset-2 hover:opacity-80"
      >
        {t(I18nKey.LICENSE$OPEN_CUSTOMER_CENTER)}
      </a>
    </div>
  );
}
