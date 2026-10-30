import React from "react";
import { useTranslation } from "react-i18next";
import { ServiceAccount } from "#/api/organization-service/service-accounts.api";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { useSetServiceAccountBudget } from "#/hooks/mutation/use-service-account-budget-mutations";
import { I18nKey } from "#/i18n/declaration";

/**
 * Monthly budget of one service account. The budget comes with the service
 * account (`monthly_limit`, `budget_limit_disabled`). Saving sets the budget
 * override, "Remove limit" deletes it. An "unlimited" override is shown
 * explicitly and stays until a limit is saved or the override is removed.
 */
export function ServiceAccountBudget({ account }: { account: ServiceAccount }) {
  const { t } = useTranslation();
  const { mutate: setBudget, isPending } = useSetServiceAccountBudget(
    account.id,
  );
  const [draft, setDraft] = React.useState<string | null>(null);

  const isUnlimited = account.budget_limit_disabled;
  const savedLimit = account.monthly_limit;
  const hasOverride = isUnlimited || savedLimit !== null;
  const value = draft ?? (savedLimit === null ? "" : String(savedLimit));
  const parsed = Number(value);
  const isValid = value !== "" && Number.isFinite(parsed) && parsed > 0;

  return (
    <section className="flex flex-col gap-2">
      <div>
        <h4 className="text-sm font-medium">
          {t(I18nKey.SERVICE_ACCOUNTS$BUDGET_HEADER)}
        </h4>
        <p className="text-xs text-muted">
          {t(I18nKey.SERVICE_ACCOUNTS$BUDGET_HELP)}
        </p>
        {isUnlimited && (
          <p
            data-testid="service-account-budget-unlimited"
            className="text-xs text-muted"
          >
            {t(I18nKey.SERVICE_ACCOUNTS$BUDGET_UNLIMITED)}
          </p>
        )}
      </div>
      <div className="flex items-end gap-4">
        <SettingsInput
          testId="service-account-budget-input"
          label={t(I18nKey.SERVICE_ACCOUNTS$BUDGET_LABEL)}
          type="number"
          // Save needs a value above 0; the API stores decimal limits.
          min={0.01}
          step={0.01}
          className="max-w-[200px]"
          value={value}
          onChange={setDraft}
        />
        <BrandButton
          testId="service-account-budget-save"
          type="button"
          variant="primary"
          isDisabled={!isValid || isPending}
          onClick={() =>
            setBudget(
              { monthlyLimit: parsed },
              { onSuccess: () => setDraft(null) },
            )
          }
        >
          {t(I18nKey.BUTTON$SAVE)}
        </BrandButton>
        {hasOverride && (
          <BrandButton
            testId="service-account-budget-remove"
            type="button"
            variant="secondary"
            isDisabled={isPending}
            onClick={() =>
              setBudget(
                { monthlyLimit: null },
                { onSuccess: () => setDraft(null) },
              )
            }
          >
            {t(I18nKey.SERVICE_ACCOUNTS$BUDGET_REMOVE)}
          </BrandButton>
        )}
      </div>
    </section>
  );
}
