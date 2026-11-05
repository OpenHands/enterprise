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

const INVALID_REASON_KEYS: Record<string, I18nKey> = {
  revoked: I18nKey.LICENSE$REVOKED,
  trial_expired: I18nKey.LICENSE$TRIAL_EXPIRED,
  subscription_cancelled: I18nKey.LICENSE$SUBSCRIPTION_CANCELLED,
  no_subscription: I18nKey.LICENSE$NO_SUBSCRIPTION,
};

/** The banner to show for a license status, or null when nothing needs saying. */
export function getLicenseBannerMessage(
  status: LicenseStatus | undefined,
): LicenseBannerMessage | null {
  if (!status) return null;
  switch (status.state) {
    case "missing_key":
      return { tone: "error", key: I18nKey.LICENSE$MISSING_KEY };
    case "invalid":
      return {
        tone: "error",
        key:
          (status.reason ? INVALID_REASON_KEYS[status.reason] : undefined) ??
          I18nKey.LICENSE$INVALID_KEY,
      };
    case "unverified":
      return { tone: "warning", key: I18nKey.LICENSE$UNVERIFIED };
    case "valid":
      if (
        status.subscription_state === "trial_active" ||
        status.subscription_state === "trial_extended"
      ) {
        return status.trial_ends_at
          ? {
              tone: "info",
              key: I18nKey.LICENSE$TRIAL_ENDS,
              date: status.trial_ends_at,
            }
          : null;
      }
      if (status.subscription_state === "subscription_active_overdue") {
        return {
          tone: "warning",
          key: I18nKey.LICENSE$PAYMENT_OVERDUE,
          date: status.overdue_since ?? undefined,
        };
      }
      if (status.subscription_state === "subscription_inactive_overdue") {
        return {
          tone: "error",
          key: I18nKey.LICENSE$PAYMENT_OVERDUE,
          date: status.overdue_since ?? undefined,
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

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * A full-width strip that reports license problems, the trial end date, or an
 * overdue payment. It can't be dismissed. It renders nothing when license
 * enforcement is off or the license is valid and paid.
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
          date: message.date ? formatDate(message.date) : "",
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
