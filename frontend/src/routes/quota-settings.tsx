import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuotaStatus } from "#/hooks/query/use-quota-status";
import { useCreateQuotaIncreaseRequest } from "#/hooks/mutation/use-create-quota-increase-request";
import { useConfig } from "#/hooks/query/use-config";
import { I18nKey } from "#/i18n/declaration";
import { displayErrorToast } from "#/utils/custom-toast-handlers";

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

function QuotaIncreaseRequestForm({
  dailyLimit,
  latestRequestStatus,
  latestRequestedLimit,
}: {
  dailyLimit: number | null;
  latestRequestStatus: string | null;
  latestRequestedLimit: number | null;
}) {
  const { t } = useTranslation();
  const createRequest = useCreateQuotaIncreaseRequest();
  const [workEmail, setWorkEmail] = useState("");
  const [requestedLimit, setRequestedLimit] = useState<number>(0);
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (dailyLimit && !requestedLimit) {
      setRequestedLimit(dailyLimit * 5);
    }
  }, [dailyLimit, requestedLimit]);

  const maxLimit = dailyLimit ? dailyLimit * 10 : 0;
  const hasPending = latestRequestStatus === "pending";

  if (dailyLimit === null || dailyLimit === 0) {
    return null;
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!workEmail.trim() || !requestedLimit) return;
    if (hasPending) {
      displayErrorToast(t(I18nKey.SETTINGS$QUOTA_REQUEST_PENDING));
      return;
    }
    createRequest.mutate({
      work_email: workEmail.trim(),
      requested_limit: requestedLimit,
      reason: reason.trim() || undefined,
    });
  };

  return (
    <div
      className="flex flex-col gap-4 rounded-lg border border-tertiary p-4"
      data-testid="quota-increase-form"
    >
      <h2 className="text-base font-semibold">
        {t(I18nKey.SETTINGS$QUOTA_REQUEST_TITLE)}
      </h2>
      <p className="text-sm text-tertiary">
        {t(I18nKey.SETTINGS$QUOTA_REQUEST_DESCRIPTION)}
      </p>

      {latestRequestStatus === "approved" && latestRequestedLimit && (
        <div
          className="rounded-sm border border-green-500 bg-green-100 px-3 py-2 text-sm text-green-700"
          data-testid="quota-request-approved"
        >
          {t(I18nKey.SETTINGS$QUOTA_REQUEST_APPROVED)} ({latestRequestedLimit})
        </div>
      )}

      {hasPending && (
        <div
          className="rounded-sm border border-yellow-500 bg-yellow-100 px-3 py-2 text-sm text-yellow-700"
          data-testid="quota-request-pending"
        >
          {t(I18nKey.SETTINGS$QUOTA_REQUEST_PENDING)}
        </div>
      )}

      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-sm text-tertiary">
            {t(I18nKey.SETTINGS$QUOTA_WORK_EMAIL)}
          </label>
          <input
            type="email"
            value={workEmail}
            onChange={(e) => setWorkEmail(e.target.value)}
            className="text-base text-white p-2 bg-base-tertiary rounded-sm border border-tertiary"
            placeholder="you@company.com"
            data-testid="quota-work-email-input"
            disabled={hasPending}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-sm text-tertiary">
            {t(I18nKey.SETTINGS$QUOTA_REQUESTED_LIMIT)}
          </label>
          <input
            type="number"
            value={requestedLimit}
            min={dailyLimit}
            max={maxLimit}
            onChange={(e) => setRequestedLimit(Number(e.target.value))}
            className="text-base text-white p-2 bg-base-tertiary rounded-sm border border-tertiary"
            data-testid="quota-requested-limit-input"
            disabled={hasPending}
          />
          <span className="text-xs text-tertiary">
            {t(I18nKey.SETTINGS$QUOTA_MAX_ALLOWED)}: {maxLimit}
          </span>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-sm text-tertiary">
            {t(I18nKey.SETTINGS$QUOTA_REASON)} (
            {t(I18nKey.SETTINGS$QUOTA_OPTIONAL)})
          </label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="text-base text-white p-2 bg-base-tertiary rounded-sm border border-tertiary"
            rows={3}
            data-testid="quota-reason-input"
            disabled={hasPending}
          />
        </div>

        <button
          type="submit"
          disabled={
            createRequest.isPending ||
            hasPending ||
            !workEmail.trim() ||
            !requestedLimit
          }
          className="px-4 py-2 rounded-sm bg-primary text-white hover:opacity-80 disabled:opacity-30 disabled:cursor-not-allowed"
          data-testid="quota-submit-request"
        >
          {createRequest.isPending
            ? t(I18nKey.SETTINGS$QUOTA_SUBMITTING)
            : t(I18nKey.SETTINGS$QUOTA_SUBMIT)}
        </button>
      </form>
    </div>
  );
}

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

      <QuotaIncreaseRequestForm
        dailyLimit={quota.daily_limit}
        latestRequestStatus={quota.latest_request_status}
        latestRequestedLimit={quota.latest_request_requested_limit}
      />
    </div>
  );
}

export default QuotaSettingsScreen;
