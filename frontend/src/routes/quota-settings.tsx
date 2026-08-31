import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuotaStatus } from "#/hooks/query/use-quota-status";
import { useCreateQuotaIncreaseRequest } from "#/hooks/mutation/use-create-quota-increase-request";
import { useConfig } from "#/hooks/query/use-config";
import { I18nKey } from "#/i18n/declaration";
import { displayErrorToast } from "#/utils/custom-toast-handlers";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { BrandButton } from "#/components/features/settings/brand-button";
import { cn } from "#/utils/utils";
import {
  formControlBorderClassName,
  formControlMultilineFieldClassName,
  formControlRadiusClassName,
  formControlSurfaceClassName,
} from "#/utils/form-control-classes";

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
      className={cn(
        formControlBorderClassName,
        formControlRadiusClassName,
        formControlSurfaceClassName,
        "flex flex-col gap-4 p-4",
      )}
      data-testid="quota-increase-form"
    >
      <h2 className="text-base font-semibold text-white">
        {t(I18nKey.SETTINGS$QUOTA_REQUEST_TITLE)}
      </h2>
      <p className="text-sm text-muted">
        {t(I18nKey.SETTINGS$QUOTA_REQUEST_DESCRIPTION)}
      </p>

      {latestRequestStatus === "approved" && latestRequestedLimit && (
        <div
          className="rounded bg-emerald-500/20 px-3 py-2 text-sm text-[var(--oh-status-success)]"
          data-testid="quota-request-approved"
        >
          {t(I18nKey.SETTINGS$QUOTA_REQUEST_APPROVED)} ({latestRequestedLimit})
        </div>
      )}

      {hasPending && (
        <div
          className="rounded bg-[var(--oh-interactive-hover)] px-3 py-2 text-sm text-[var(--oh-muted)]"
          data-testid="quota-request-pending"
        >
          {t(I18nKey.SETTINGS$QUOTA_REQUEST_PENDING)}
        </div>
      )}

      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <SettingsInput
          testId="quota-work-email-input"
          type="email"
          label={t(I18nKey.SETTINGS$QUOTA_WORK_EMAIL)}
          value={workEmail}
          onChange={setWorkEmail}
          placeholder="you@company.com"
          isDisabled={hasPending}
        />

        <SettingsInput
          testId="quota-requested-limit-input"
          type="number"
          label={t(I18nKey.SETTINGS$QUOTA_REQUESTED_LIMIT)}
          value={String(requestedLimit)}
          min={dailyLimit}
          max={maxLimit}
          onChange={(value) => setRequestedLimit(Number(value))}
          isDisabled={hasPending}
          hint={`${t(I18nKey.SETTINGS$QUOTA_MAX_ALLOWED)}: ${maxLimit}`}
        />

        <label className="flex flex-col gap-2.5">
          <span className="text-sm">
            {t(I18nKey.SETTINGS$QUOTA_REASON)} (
            {t(I18nKey.SETTINGS$QUOTA_OPTIONAL)})
          </span>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className={cn(formControlMultilineFieldClassName, "resize-none")}
            rows={3}
            data-testid="quota-reason-input"
            disabled={hasPending}
          />
        </label>

        <BrandButton
          testId="quota-submit-request"
          type="submit"
          variant="primary"
          isDisabled={
            createRequest.isPending ||
            hasPending ||
            !workEmail.trim() ||
            !requestedLimit
          }
        >
          {createRequest.isPending
            ? t(I18nKey.SETTINGS$QUOTA_SUBMITTING)
            : t(I18nKey.SETTINGS$QUOTA_SUBMIT)}
        </BrandButton>
      </form>
    </div>
  );
}

function QuotaSettingsScreen() {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const { data: quota, isLoading } = useQuotaStatus();
  const unlimited = quota?.daily_limit === null;
  const countdown = useCountdown(unlimited ? null : (quota?.reset_at ?? null));

  const isSaas = config?.app_mode === "saas";

  if (!isSaas) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-muted">{t(I18nKey.SETTINGS$QUOTA_SALES_ONLY)}</p>
      </div>
    );
  }

  if (isLoading || !quota) {
    return (
      <div className="flex h-full items-center justify-center">
        <div
          className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--oh-border)] border-t-primary"
          data-testid="quota-loading"
        />
      </div>
    );
  }

  const limit = quota.daily_limit ?? 0;
  const pct =
    unlimited || limit === 0
      ? 0
      : Math.min((quota.used_today / limit) * 100, 100);

  return (
    <div className="flex flex-col gap-6">
      <div
        className={cn(
          formControlBorderClassName,
          formControlRadiusClassName,
          formControlSurfaceClassName,
          "flex flex-col gap-3 p-4",
        )}
        data-testid="quota-status-card"
      >
        <div className="flex items-baseline justify-between">
          <span className="text-sm text-muted">
            {t(I18nKey.SETTINGS$QUOTA_DAILY_LIMIT)}
          </span>
          <span className="text-lg font-semibold" data-testid="quota-limit">
            {unlimited
              ? t(I18nKey.SETTINGS$QUOTA_UNLIMITED)
              : quota.daily_limit}
          </span>
        </div>

        <div className="flex items-baseline justify-between">
          <span className="text-sm text-muted">
            {t(I18nKey.SETTINGS$QUOTA_USED_TODAY)}
          </span>
          <span className="text-lg font-semibold" data-testid="quota-used">
            {quota.used_today}
          </span>
        </div>

        <div className="flex items-baseline justify-between">
          <span className="text-sm text-muted">
            {t(I18nKey.SETTINGS$QUOTA_REMAINING)}
          </span>
          <span className="text-lg font-semibold" data-testid="quota-remaining">
            {unlimited ? t(I18nKey.SETTINGS$QUOTA_UNLIMITED) : quota.remaining}
          </span>
        </div>

        {!unlimited && (
          <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-[var(--oh-interactive-hover-low)]">
            <div
              className="h-full rounded-full bg-primary transition-all"
              style={{ width: `${pct}%` }}
              data-testid="quota-progress-bar"
            />
          </div>
        )}
      </div>

      {!unlimited && (
        <div
          className="flex items-center gap-2 text-sm text-muted"
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
      )}

      <QuotaIncreaseRequestForm
        dailyLimit={quota.daily_limit}
        latestRequestStatus={quota.latest_request_status}
        latestRequestedLimit={quota.latest_request_requested_limit}
      />
    </div>
  );
}

export default QuotaSettingsScreen;
