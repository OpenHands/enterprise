import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuotaStatus } from "#/hooks/query/use-quota-status";
import { useConfig } from "#/hooks/query/use-config";
import { I18nKey } from "#/i18n/declaration";

function useCountdown(resetAt: string | null) {
  const [remaining, setRemaining] = useState<string>("");

  useEffect(() => {
    if (!resetAt) {
      setRemaining("");
      return undefined;
    }

    const update = () => {
      const diff = new Date(resetAt).getTime() - Date.now();
      if (diff <= 0) {
        setRemaining("00:00:00");
        return;
      }
      const h = Math.floor(diff / 3_600_000);
      const m = Math.floor((diff % 3_600_000) / 60_000);
      const s = Math.floor((diff % 60_000) / 1_000);
      setRemaining(
        `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`,
      );
    };

    update();
    const id = setInterval(update, 1_000);
    return () => clearInterval(id);
  }, [resetAt]);

  return remaining;
}

const QUOTA_INCREASE_REQUEST_URL =
  "https://u8mk1.share.hsforms.com/2lXOvoRtHRfeWEmba8CdOGw";

function QuotaSettingsScreen() {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const { data: quota, isLoading } = useQuotaStatus();
  const countdown = useCountdown(quota?.reset_at ?? null);

  const isSaas = config?.app_mode === "saas";

  if (!isSaas) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-tertiary">{t(I18nKey.SETTINGS$QUOTA_SALES_ONLY)}</p>
      </div>
    );
  }

  if (isLoading || !quota) {
    return (
      <div className="flex h-full items-center justify-center">
        <div
          className="h-6 w-6 animate-spin rounded-full border-2 border-tertiary border-t-primary"
          data-testid="quota-loading"
        />
      </div>
    );
  }

  const unlimited = quota.daily_limit === null;
  const limit = quota.daily_limit ?? 0;
  const pct =
    unlimited || limit === 0
      ? 0
      : Math.min((quota.used_today / limit) * 100, 100);

  return (
    <div className="flex flex-col gap-6 p-4 max-w-2xl">
      <h1 className="text-xl font-bold" data-testid="quota-title">
        {t(I18nKey.SETTINGS$NAV_QUOTA)}
      </h1>

      <div
        className="flex flex-col gap-3 rounded-lg border border-tertiary p-4"
        data-testid="quota-status-card"
      >
        <div className="flex items-baseline justify-between">
          <span className="text-sm text-tertiary">
            {t(I18nKey.SETTINGS$QUOTA_DAILY_LIMIT)}
          </span>
          <span className="text-lg font-semibold" data-testid="quota-limit">
            {unlimited
              ? t(I18nKey.SETTINGS$QUOTA_UNLIMITED)
              : quota.daily_limit}
          </span>
        </div>

        <div className="flex items-baseline justify-between">
          <span className="text-sm text-tertiary">
            {t(I18nKey.SETTINGS$QUOTA_USED_TODAY)}
          </span>
          <span className="text-lg font-semibold" data-testid="quota-used">
            {quota.used_today}
          </span>
        </div>

        <div className="flex items-baseline justify-between">
          <span className="text-sm text-tertiary">
            {t(I18nKey.SETTINGS$QUOTA_REMAINING)}
          </span>
          <span className="text-lg font-semibold" data-testid="quota-remaining">
            {unlimited ? t(I18nKey.SETTINGS$QUOTA_UNLIMITED) : quota.remaining}
          </span>
        </div>

        {!unlimited && (
          <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-base-tertiary">
            <div
              className="h-full rounded-full bg-primary transition-all"
              style={{ width: `${pct}%` }}
              data-testid="quota-progress-bar"
            />
          </div>
        )}
      </div>

      <div
        className="flex items-center gap-2 text-sm text-tertiary"
        data-testid="quota-reset-countdown"
      >
        <span>{t(I18nKey.SETTINGS$QUOTA_RESETS_IN)}</span>
        <span
          className="font-mono font-semibold text-primary"
          data-testid="quota-countdown"
        >
          {countdown}
        </span>
      </div>

      <a
        href={QUOTA_INCREASE_REQUEST_URL}
        target="_blank"
        rel="noreferrer"
        className="text-sm text-primary underline hover:opacity-80"
        data-testid="quota-increase-request-link"
      >
        {t(I18nKey.SETTINGS$QUOTA_REQUEST_TITLE)}
      </a>

    </div>
  );
}

export default QuotaSettingsScreen;
