import React from "react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { useServiceAccountBudget } from "#/hooks/query/use-service-account-budget";
import { useSetServiceAccountBudget } from "#/hooks/mutation/use-service-account-budget-mutations";
import { I18nKey } from "#/i18n/declaration";

/**
 * Monthly budget of one service account. Reuses the budget override API of
 * the Budgets page: saving sets the override, "Remove limit" deletes it.
 */
export function ServiceAccountBudget({
  saId,
  name,
}: {
  saId: string;
  name: string;
}) {
  const { t } = useTranslation();
  const { data: row, isSuccess } = useServiceAccountBudget(saId, name, true);
  const { mutate: setBudget, isPending } = useSetServiceAccountBudget(saId);
  const [draft, setDraft] = React.useState<string | null>(null);

  const savedLimit = row?.is_override ? row.monthly_limit : null;
  const value = draft ?? (savedLimit === null ? "" : String(savedLimit));
  const parsed = Number(value);
  const isValid = value !== "" && Number.isFinite(parsed) && parsed >= 0;

  return (
    <section className="flex flex-col gap-2">
      <div>
        <h4 className="text-sm font-medium">
          {t(I18nKey.SERVICE_ACCOUNTS$BUDGET_HEADER)}
        </h4>
        <p className="text-xs text-muted">
          {t(I18nKey.SERVICE_ACCOUNTS$BUDGET_HELP)}
        </p>
      </div>
      <div className="flex items-end gap-4">
        <SettingsInput
          testId="service-account-budget-input"
          label={t(I18nKey.SERVICE_ACCOUNTS$BUDGET_LABEL)}
          type="number"
          min={0}
          step={1}
          className="max-w-[200px]"
          value={value}
          onChange={setDraft}
          isDisabled={!isSuccess}
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
        {savedLimit !== null && (
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
