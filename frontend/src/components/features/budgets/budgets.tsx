/* eslint-disable i18next/no-literal-string */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { useBudgetSettings } from "#/hooks/query/use-budget-control";
import { useDebounce } from "#/hooks/use-debounce";
import { BudgetControlPanel } from "./budget-control-panel";
import { BudgetUsageSummary } from "./budget-usage-summary";
import { BudgetNotifications } from "./budget-notifications";

function OrganizationBudgets({ orgId }: { orgId: string }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, 300);
  const budget = useBudgetSettings(orgId, page, debouncedSearch.trim());
  if (budget.isPending) return <p>{t(I18nKey.HOME$LOADING)}</p>;
  if (budget.isError)
    return (
      <div className="space-y-3">
        <p role="alert">{t(I18nKey.BUDGET_CONTROL$STATUS_UNAVAILABLE)}</p>
        <button type="button" onClick={() => budget.refetch()}>
          {t(I18nKey.BUTTON$REFRESH)}
        </button>
      </div>
    );
  const pages = Math.max(
    1,
    Math.ceil(budget.data.users_total / budget.data.users_per_page),
  );
  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">
        {t(I18nKey.SETTINGS$NAV_BUDGETS)}
      </h1>
      <BudgetUsageSummary budget={budget.data} />
      <div className="flex flex-wrap items-center gap-3">
        <label>
          Search members
          <input
            type="search"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
            className="ml-3 rounded border border-neutral-600 bg-transparent p-2"
          />
        </label>
        <button
          type="button"
          disabled={page <= 1 || budget.isFetching}
          onClick={() => setPage((value) => value - 1)}
        >
          {t(I18nKey.COMMON$BACK)}
        </button>
        <span>
          {page} / {pages}
        </span>
        <button
          type="button"
          disabled={page >= pages || budget.isFetching}
          onClick={() => setPage((value) => value + 1)}
        >
          {t(I18nKey.ORG$NEXT)}
        </button>
      </div>
      <BudgetControlPanel
        key={orgId}
        orgId={orgId}
        budget={budget.data}
        disabled={budget.isPlaceholderData}
      />
      <BudgetNotifications key={orgId} orgId={orgId} />
    </div>
  );
}

export function Budgets() {
  const { organizationId } = useSelectedOrganizationId();
  if (!organizationId) return <p>Select an organization to manage budgets.</p>;
  return <OrganizationBudgets key={organizationId} orgId={organizationId} />;
}
