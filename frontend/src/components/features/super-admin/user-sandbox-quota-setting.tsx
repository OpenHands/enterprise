import { useState } from "react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { useUserQuota } from "#/hooks/query/use-quota-status";
import { useUpdateUserQuota } from "#/hooks/mutation/use-update-user-quota";
import { I18nKey } from "#/i18n/declaration";

export function UserSandboxQuotaSetting({
  userId,
  disabled,
}: {
  userId: string;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const quota = useUserQuota(userId);
  const update = useUpdateUserQuota();
  // An untouched form follows server data; edits survive background refetches.
  const [draft, setDraft] = useState<{ mode: string; custom: string } | null>(
    null,
  );
  const saved = quota.data?.daily_sandbox_limit;
  const mode =
    draft?.mode ??
    (saved === -1 ? "unlimited" : saved == null ? "inherit" : "custom");
  const custom =
    draft?.custom ?? (saved != null && saved > 0 ? String(saved) : "");
  const validCustom =
    /^\d+$/.test(custom) && Number(custom) >= 1 && Number(custom) <= 2147483647;
  const value =
    mode === "inherit" ? null : mode === "unlimited" ? -1 : Number(custom);
  const invalid = mode === "custom" && !validCustom;
  const busy = disabled || update.isPending;
  const edit = (nextMode: string, nextCustom: string) => {
    update.reset();
    setDraft({ mode: nextMode, custom: nextCustom });
  };
  return (
    <section
      className="flex flex-col gap-2 border-b border-[var(--oh-border)] pb-5"
      aria-label={t(I18nKey.SUPER_ADMIN$SANDBOX_QUOTA)}
    >
      <h4 className="text-sm font-medium">
        {t(I18nKey.SUPER_ADMIN$SANDBOX_QUOTA)}
      </h4>
      <p id={`quota-hint-${userId}`} className="text-xs text-[var(--oh-muted)]">
        {t(I18nKey.SUPER_ADMIN$SANDBOX_QUOTA_HINT)}
      </p>
      {quota.isPending ? (
        <p role="status">{t(I18nKey.SUPER_ADMIN$LOADING)}</p>
      ) : null}
      {quota.isError ? (
        <div role="alert" className="text-sm text-red-400">
          <p>{t(I18nKey.SUPER_ADMIN$SANDBOX_QUOTA_LOAD_ERROR)}</p>
          <BrandButton
            type="button"
            variant="secondary"
            onClick={() => quota.refetch()}
            isDisabled={quota.isFetching}
          >
            {t(I18nKey.CONVERSATION$RETRY)}
          </BrandButton>
        </div>
      ) : null}
      {quota.isSuccess ? (
        <>
          <select
            aria-label={t(I18nKey.SUPER_ADMIN$SANDBOX_QUOTA)}
            aria-describedby={`quota-hint-${userId}`}
            value={mode}
            disabled={busy}
            onChange={(event) => edit(event.target.value, custom)}
            className="rounded-md border border-[var(--oh-border)] bg-[var(--oh-bg)] p-2 text-sm"
          >
            <option value="inherit">
              {t(I18nKey.SUPER_ADMIN$SANDBOX_QUOTA_INHERIT)}
            </option>
            <option value="unlimited">
              {t(I18nKey.SUPER_ADMIN$SANDBOX_QUOTA_UNLIMITED)}
            </option>
            <option value="custom">
              {t(I18nKey.SUPER_ADMIN$SANDBOX_QUOTA_CUSTOM)}
            </option>
          </select>
          {mode === "custom" ? (
            <>
              <label htmlFor={`quota-limit-${userId}`} className="text-xs">
                {t(I18nKey.SUPER_ADMIN$SANDBOX_QUOTA_LIMIT)}
              </label>
              <input
                id={`quota-limit-${userId}`}
                type="text"
                inputMode="numeric"
                value={custom}
                disabled={busy}
                aria-invalid={invalid}
                aria-describedby={invalid ? `quota-error-${userId}` : undefined}
                onChange={(event) => edit(mode, event.target.value)}
                className="rounded-md border border-[var(--oh-border)] bg-[var(--oh-bg)] p-2 text-sm"
              />
              {invalid ? (
                <p
                  id={`quota-error-${userId}`}
                  role="alert"
                  className="text-xs text-red-400"
                >
                  {t(I18nKey.SUPER_ADMIN$SANDBOX_QUOTA_INVALID)}
                </p>
              ) : null}
            </>
          ) : null}
          <BrandButton
            type="button"
            variant="secondary"
            isDisabled={busy || invalid || value === saved}
            onClick={() =>
              update.mutate(
                { userId, dailySandboxLimit: value },
                { onSuccess: () => setDraft(null) },
              )
            }
          >
            {update.isPending
              ? t(I18nKey.SUPER_ADMIN$SANDBOX_QUOTA_SAVING)
              : t(I18nKey.BUTTON$SAVE)}
          </BrandButton>
        </>
      ) : null}
      {update.isError ? (
        <p role="alert" className="text-sm text-red-400">
          {t(I18nKey.SUPER_ADMIN$SANDBOX_QUOTA_SAVE_ERROR)}
        </p>
      ) : null}
      {update.isSuccess ? (
        <p role="status" className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$SANDBOX_QUOTA_SAVED)}
        </p>
      ) : null}
    </section>
  );
}
