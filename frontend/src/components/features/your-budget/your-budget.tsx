import React from "react";
import { useTranslation } from "react-i18next";
import type {
  OrgMyBudget,
  OrgMyUsageStats,
} from "#/api/organization-service/organization-service.api";
import {
  AGENT_COLORS,
  formatCost,
  formatDateTime,
  formatShortDate,
} from "#/components/features/admin-dashboard/usage-dashboard-utils";
import { SpendMeter } from "#/components/features/budgets/budgets-components";
import { useMyBudget } from "#/hooks/query/use-my-budget";
import { useMyUsage } from "#/hooks/query/use-my-usage";
import { useOrgTypeAndAccess } from "#/hooks/use-org-type-and-access";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import { formatTimeDelta } from "#/utils/format-time-delta";
import { cn } from "#/utils/utils";

const DAY_MS = 86_400_000;
const EMPTY_VALUE = "—";
const CARD_CLASS_NAME =
  "rounded-lg border border-border-subtle bg-base-secondary";

// The cards always describe the current budget cycle; the selector only
// scopes the usage breakdown below them.
const TIME_WINDOWS = [
  {
    value: "7d",
    label: I18nKey.SETTINGS$YOUR_BUDGET_WEEK,
    description: I18nKey.SETTINGS$YOUR_BUDGET_LAST_7_DAYS,
  },
  {
    value: "30d",
    label: I18nKey.SETTINGS$YOUR_BUDGET_MONTH,
    description: I18nKey.SETTINGS$YOUR_BUDGET_LAST_30_DAYS,
  },
  {
    value: "ytd",
    label: I18nKey.SETTINGS$YOUR_BUDGET_YEAR,
    description: I18nKey.SETTINGS$YOUR_BUDGET_YEAR_TO_DATE,
  },
] as const;

type TimeWindow = (typeof TIME_WINDOWS)[number];

const formatLongDate = (value: string) =>
  new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

function Spinner({ testId }: { testId: string }) {
  return (
    <div className="flex items-center justify-center py-8">
      <div
        className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--oh-border)] border-t-primary"
        data-testid={testId}
      />
    </div>
  );
}

function StatCard({
  label,
  value,
  sublines,
  testId,
}: {
  label: string;
  value: string;
  sublines: (string | null)[];
  testId: string;
}) {
  return (
    <div
      className={cn(CARD_CLASS_NAME, "flex flex-col gap-2 px-4 py-5")}
      data-testid={testId}
    >
      <span className="text-xs font-medium uppercase tracking-wide text-muted">
        {label}
      </span>
      <span className="text-2xl font-bold leading-none text-foreground">
        {value}
      </span>
      {sublines.filter(Boolean).map((subline) => (
        <span key={subline} className="text-xs text-muted">
          {subline}
        </span>
      ))}
    </div>
  );
}

function BudgetSummary({ budget }: { budget: OrgMyBudget }) {
  const { t } = useTranslation();

  const limit = budget.monthly_limit ?? null;
  const spend = budget.current_spend ?? null;
  const remaining =
    limit !== null && spend !== null ? Math.max(limit - spend, 0) : null;
  const percentage = limit && spend !== null ? (spend / limit) * 100 : null;

  const now = Date.now();
  const elapsedDays = budget.cycle_start_at
    ? Math.max((now - new Date(budget.cycle_start_at).getTime()) / DAY_MS, 1)
    : null;
  const dailyRate = spend && elapsedDays ? spend / elapsedDays : null;
  const daysUntilReset = budget.cycle_end_at
    ? Math.max((new Date(budget.cycle_end_at).getTime() - now) / DAY_MS, 0)
    : null;

  let allocationNote: string = t(I18nKey.SETTINGS$YOUR_BUDGET_ORG_DEFAULT);
  if (budget.is_disabled) {
    allocationNote = t(I18nKey.SETTINGS$YOUR_BUDGET_EXEMPT);
  } else if (limit === null) {
    allocationNote = t(I18nKey.SETTINGS$YOUR_BUDGET_NO_LIMIT_SET);
  } else if (budget.is_override && budget.limit_updated_at) {
    allocationNote = t(I18nKey.SETTINGS$YOUR_BUDGET_SET_BY_ADMIN_ON, {
      date: formatLongDate(budget.limit_updated_at),
    });
  }

  let spendNote: string | null = null;
  if (spend === null) {
    spendNote = t(I18nKey.SETTINGS$YOUR_BUDGET_SPEND_UNAVAILABLE);
  } else if (dailyRate !== null) {
    spendNote = t(I18nKey.SETTINGS$YOUR_BUDGET_PER_WEEK, {
      amount: formatCost(dailyRate * 7),
    });
  }
  const staleNote =
    budget.spend_status === "stale" && budget.spend_observed_at
      ? t(I18nKey.SETTINGS$YOUR_BUDGET_SPEND_AS_OF, {
          time: formatDateTime(budget.spend_observed_at),
        })
      : null;

  let remainingNote: string | null = null;
  if (remaining !== null && dailyRate !== null && daysUntilReset !== null) {
    const daysAtRate = remaining / dailyRate;
    remainingNote =
      daysAtRate >= daysUntilReset
        ? t(I18nKey.SETTINGS$YOUR_BUDGET_ENOUGH_FOR_CYCLE)
        : t(I18nKey.SETTINGS$YOUR_BUDGET_DAYS_AT_RATE, {
            days: Math.floor(daysAtRate),
          });
  }

  // Same thresholds as the admin Budgets page.
  let status = {
    text: t(I18nKey.SETTINGS$YOUR_BUDGET_STATUS_ON_TRACK),
    className: "text-success",
  };
  if (percentage !== null && percentage > 100) {
    status = {
      text: t(I18nKey.SETTINGS$YOUR_BUDGET_STATUS_OVER_CAP),
      className: "text-danger",
    };
  } else if (percentage !== null && percentage >= 90) {
    status = {
      text: t(I18nKey.SETTINGS$YOUR_BUDGET_STATUS_OVER_90),
      className: "text-danger",
    };
  } else if (percentage !== null && percentage >= 80) {
    status = {
      text: t(I18nKey.SETTINGS$YOUR_BUDGET_STATUS_OVER_80),
      className: "text-logo",
    };
  }

  return (
    <>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <StatCard
          testId="your-budget-allocation"
          label={t(I18nKey.SETTINGS$YOUR_BUDGET_ALLOCATION)}
          value={
            limit === null
              ? t(I18nKey.SETTINGS$YOUR_BUDGET_NO_LIMIT)
              : formatCost(limit)
          }
          sublines={[allocationNote]}
        />
        <StatCard
          testId="your-budget-spent"
          label={t(I18nKey.SETTINGS$YOUR_BUDGET_SPENT)}
          value={spend === null ? EMPTY_VALUE : formatCost(spend)}
          sublines={[spendNote, staleNote]}
        />
        <StatCard
          testId="your-budget-remaining"
          label={t(I18nKey.SETTINGS$YOUR_BUDGET_REMAINING)}
          value={remaining === null ? EMPTY_VALUE : formatCost(remaining)}
          sublines={[remainingNote]}
        />
      </div>

      {limit !== null && (
        <div
          className={cn(CARD_CLASS_NAME, "flex flex-col gap-3 p-4")}
          data-testid="your-budget-meter"
        >
          {percentage === null ? (
            <div className="h-3 rounded-full bg-tertiary" />
          ) : (
            // The shared tick labels are evenly spaced rather than placed at
            // 80/90/100%, which misreads next to the fill; show the exact
            // percentage instead.
            <SpendMeter percentage={percentage} showTicks={false} />
          )}
          <div className="flex items-center justify-between text-xs">
            {percentage !== null && (
              <span
                className={cn("font-medium", status.className)}
                data-testid="your-budget-status"
              >
                {`${status.text} · ${Math.round(percentage)}%`}
              </span>
            )}
            {budget.cycle_end_at && (
              <span className="ml-auto text-muted">
                {t(I18nKey.SETTINGS$YOUR_BUDGET_RESETS_ON, {
                  date: formatLongDate(budget.cycle_end_at),
                })}
              </span>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function DailySpendChart({ days }: { days: OrgMyUsageStats["daily_spend"] }) {
  const maxCost = Math.max(...days.map((day) => day.cost), 0);
  const labelEvery = Math.ceil(days.length / 7);

  return (
    <div
      className={cn(
        "flex h-40 items-end",
        days.length > 31 ? "gap-px" : "gap-1",
      )}
      data-testid="your-budget-daily-chart"
    >
      {days.map((day, index) => (
        <div
          key={day.date}
          className="flex h-full min-w-0 flex-1 flex-col justify-end gap-1"
          title={`${formatShortDate(day.date)}: ${formatCost(day.cost)}`}
        >
          <div
            className="min-h-[2px] w-full rounded-t bg-primary"
            style={{
              height: maxCost > 0 ? `${(day.cost / maxCost) * 100}%` : 0,
            }}
          />
          <span className="h-4 whitespace-nowrap text-[10px] text-muted">
            {index % labelEvery === 0 ? formatShortDate(day.date) : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

function ModelUsageList({
  models,
}: {
  models: OrgMyUsageStats["model_usage"];
}) {
  const total = models.reduce((sum, model) => sum + model.total_cost, 0);

  return (
    <ul className="flex flex-col gap-3" data-testid="your-budget-models">
      {models.map((model, index) => {
        const color = AGENT_COLORS[index % AGENT_COLORS.length];
        return (
          <li key={model.model_name} className="flex items-center gap-3">
            <span
              className="size-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: color }}
            />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm text-foreground">
                {model.model_name}
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-tertiary">
                <div
                  className="h-full rounded-full"
                  style={{
                    backgroundColor: color,
                    width:
                      total > 0 ? `${(model.total_cost / total) * 100}%` : 0,
                  }}
                />
              </div>
            </div>
            <span className="shrink-0 text-sm font-medium text-foreground">
              {formatCost(model.total_cost)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function RecentUsageList({
  items,
}: {
  items: OrgMyUsageStats["recent_usage"];
}) {
  const { t } = useTranslation();

  return (
    <ul className="flex flex-col gap-0.5" data-testid="your-budget-recent">
      {items.map((item) => (
        <li key={item.conversation_id}>
          <a
            href={`/canvas/conversations/${item.conversation_id}`}
            className="flex items-center gap-3 rounded-md px-3 py-2.5 hover:bg-[var(--oh-interactive-hover-low)]"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm text-foreground">
                {item.title || t(I18nKey.SETTINGS$YOUR_BUDGET_UNTITLED)}
              </div>
              {item.updated_at && (
                <div className="mt-0.5 text-xs text-muted">
                  {formatTimeDelta(item.updated_at)}{" "}
                  {t(I18nKey.CONVERSATION$AGO)}
                </div>
              )}
            </div>
            <span className="shrink-0 text-sm font-medium text-foreground">
              {formatCost(item.accumulated_cost)}
            </span>
          </a>
        </li>
      ))}
    </ul>
  );
}

function UsageBreakdown({ timeWindow }: { timeWindow: TimeWindow }) {
  const { t } = useTranslation();
  const { data: usage, isLoading } = useMyUsage({
    timeWindow: timeWindow.value,
  });

  if (isLoading || !usage) {
    return <Spinner testId="your-budget-usage-loading" />;
  }

  const emptyNote = (
    <p className="text-sm text-muted">
      {t(I18nKey.SETTINGS$YOUR_BUDGET_NO_USAGE)}
    </p>
  );

  let trend: string | null = null;
  if (usage.previous_period_spend > 0) {
    const change =
      ((usage.total_spend - usage.previous_period_spend) /
        usage.previous_period_spend) *
      100;
    trend = t(
      change <= 0
        ? I18nKey.SETTINGS$YOUR_BUDGET_TREND_LESS
        : I18nKey.SETTINGS$YOUR_BUDGET_TREND_MORE,
      { percent: Math.abs(Math.round(change)) },
    );
  }

  return (
    <>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <section className={cn(CARD_CLASS_NAME, "p-5 lg:col-span-2")}>
          <div className="mb-4 flex items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-foreground">
                {t(I18nKey.SETTINGS$YOUR_BUDGET_DAILY_SPENDING)}
              </h3>
              <p className="text-xs text-muted">{t(timeWindow.description)}</p>
            </div>
            <div className="flex flex-col items-end gap-1">
              <span
                className="text-sm font-semibold text-foreground"
                data-testid="your-budget-period-total"
              >
                {formatCost(usage.total_spend)}
              </span>
              {trend && (
                <span
                  className="rounded bg-tertiary px-2 py-1 text-[11px] font-medium text-muted"
                  data-testid="your-budget-trend"
                >
                  {trend}
                </span>
              )}
            </div>
          </div>
          {usage.total_spend > 0 ? (
            <DailySpendChart days={usage.daily_spend} />
          ) : (
            emptyNote
          )}
        </section>

        <section className={cn(CARD_CLASS_NAME, "p-5")}>
          <h3 className="text-sm font-semibold text-foreground">
            {t(I18nKey.SETTINGS$YOUR_BUDGET_USAGE_BY_MODEL)}
          </h3>
          <p className="mb-4 text-xs text-muted">{t(timeWindow.description)}</p>
          {usage.model_usage.length > 0 ? (
            <ModelUsageList models={usage.model_usage} />
          ) : (
            emptyNote
          )}
        </section>
      </div>

      <section className={cn(CARD_CLASS_NAME, "p-5")}>
        <h3 className="mb-3 text-sm font-semibold text-foreground">
          {t(I18nKey.SETTINGS$YOUR_BUDGET_RECENT_USAGE)}
        </h3>
        {usage.recent_usage.length > 0 ? (
          <RecentUsageList items={usage.recent_usage} />
        ) : (
          emptyNote
        )}
      </section>

      <p className="text-xs text-muted">
        {t(I18nKey.SETTINGS$YOUR_BUDGET_ESTIMATE_NOTE)}
      </p>
    </>
  );
}

export function YourBudget() {
  const { t } = useTranslation();
  const { selectedOrg } = useOrgTypeAndAccess();
  const { data: budget, isLoading, isError } = useMyBudget();
  const [timeWindow, setTimeWindow] = React.useState<TimeWindow>(
    TIME_WINDOWS[1],
  );

  let content = <Spinner testId="your-budget-loading" />;
  if (isError) {
    content = (
      <p className="text-sm text-muted" data-testid="your-budget-error">
        {t(I18nKey.SETTINGS$YOUR_BUDGET_LOAD_ERROR)}
      </p>
    );
  } else if (!isLoading && budget) {
    content = budget.enabled ? (
      <>
        <BudgetSummary budget={budget} />
        <UsageBreakdown timeWindow={timeWindow} />
      </>
    ) : (
      <p className="text-sm text-muted" data-testid="your-budget-not-enabled">
        {t(I18nKey.SETTINGS$YOUR_BUDGET_NOT_ENABLED)}
      </p>
    );
  }

  return (
    <div data-testid="your-budget-screen" className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <header className="min-w-0 space-y-1">
          <Typography.H2>{t(I18nKey.SETTINGS$NAV_YOUR_BUDGET)}</Typography.H2>
          <p
            data-testid="settings-page-subtitle"
            className="text-sm leading-5 text-muted"
          >
            {t(I18nKey.SETTINGS$PAGE_YOUR_BUDGET_SUBLINE, {
              orgName: selectedOrg?.name ?? "",
            })}
          </p>
        </header>
        {budget?.enabled && (
          <div
            className="inline-flex shrink-0 rounded-lg border border-border-subtle bg-base-secondary p-0.5"
            data-testid="your-budget-period-selector"
          >
            {TIME_WINDOWS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setTimeWindow(option)}
                aria-pressed={option.value === timeWindow.value}
                className={cn(
                  "rounded-md px-3 py-1.5 text-xs font-medium",
                  option.value === timeWindow.value
                    ? "bg-tertiary text-foreground"
                    : "text-muted hover:text-foreground",
                )}
              >
                {t(option.label)}
              </button>
            ))}
          </div>
        )}
      </div>
      {content}
    </div>
  );
}
