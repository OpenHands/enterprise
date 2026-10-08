import { BarChart2, Wallet } from "lucide-react";
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
import { InfoTooltip } from "#/components/features/settings/info-tooltip";
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

// The third card can show either the member's own figures or the
// organization's; LiteLLM enforces both caps.
const BUDGET_SCOPES = [
  { value: "user", label: I18nKey.SETTINGS$NAV_YOUR_BUDGET },
  { value: "org", label: I18nKey.SETTINGS$YOUR_BUDGET_SCOPE_ORG },
] as const;

type BudgetScope = (typeof BUDGET_SCOPES)[number]["value"];

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

interface StatFigureProps {
  label: string;
  value: string;
  testId: string;
  sublines?: (string | null)[];
  /** Explanation shown in an info tooltip next to the label. */
  help?: string;
  /** Leading icon, shown in a circle beside the figure. */
  icon?: React.ReactNode;
  /** Sentence-case label for the compact figures inside the scope card. */
  plainLabel?: boolean;
}

function StatFigure({
  label,
  value,
  testId,
  sublines = [],
  help,
  icon,
  plainLabel = false,
}: StatFigureProps) {
  return (
    <div className="flex gap-3" data-testid={testId}>
      {icon && (
        <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-tertiary text-foreground">
          {icon}
        </span>
      )}
      <div className="flex min-w-0 flex-col gap-2">
        <span
          className={cn(
            "flex items-center gap-1.5 text-muted",
            icon ? "min-h-10" : undefined,
            plainLabel
              ? "text-sm"
              : "text-xs font-medium uppercase tracking-wide",
          )}
        >
          {label}
          {help && <InfoTooltip content={help} />}
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
    </div>
  );
}

function BudgetSummary({ budget }: { budget: OrgMyBudget }) {
  const { t } = useTranslation();
  const [scope, setScope] = React.useState<BudgetScope>("user");

  const limit = budget.monthly_limit ?? null;
  const spend = budget.current_spend ?? null;
  const remaining =
    limit !== null && spend !== null ? Math.max(limit - spend, 0) : null;
  const orgLimit = budget.org_monthly_limit ?? null;
  const orgSpend = budget.org_current_spend ?? null;
  const orgRemaining =
    orgLimit !== null && orgSpend !== null
      ? Math.max(orgLimit - orgSpend, 0)
      : null;
  // LiteLLM enforces the organization cap as well as the personal one, so
  // the tighter of the two is what can actually still be spent. When one
  // side is unknown or unlimited, the other is the only cap.
  let available = remaining ?? orgRemaining;
  if (remaining !== null && orgRemaining !== null) {
    available = Math.min(remaining, orgRemaining);
  }
  const scoped =
    scope === "user"
      ? { limit, spend, remaining }
      : { limit: orgLimit, spend: orgSpend, remaining: orgRemaining };
  const percentage =
    scoped.limit && scoped.spend !== null
      ? (scoped.spend / scoped.limit) * 100
      : null;

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
      <div className="@container">
        <div className="grid grid-cols-1 gap-4 @xl:grid-cols-2">
          <div className={cn(CARD_CLASS_NAME, "px-4 py-5")}>
            <StatFigure
              testId="your-budget-available"
              icon={<Wallet className="size-5" strokeWidth={2} aria-hidden />}
              label={t(I18nKey.SETTINGS$YOUR_BUDGET_AVAILABLE)}
              help={t(I18nKey.SETTINGS$YOUR_BUDGET_AVAILABLE_TOOLTIP)}
              value={available === null ? EMPTY_VALUE : formatCost(available)}
              sublines={[t(I18nKey.SETTINGS$YOUR_BUDGET_AVAILABLE_HELP)]}
            />
          </div>
          <div className={cn(CARD_CLASS_NAME, "px-4 py-5")}>
            <StatFigure
              testId="your-budget-spent"
              icon={
                <BarChart2 className="size-5" strokeWidth={2} aria-hidden />
              }
              label={t(I18nKey.SETTINGS$YOUR_BUDGET_SPENT)}
              value={spend === null ? EMPTY_VALUE : formatCost(spend)}
              sublines={[spendNote, staleNote]}
            />
          </div>
          {/* The scope card needs the most room, so it takes a full row. */}
          <div
            className={cn(
              CARD_CLASS_NAME,
              "flex flex-col gap-4 px-4 py-5 @xl:col-span-2",
            )}
            data-testid="your-budget-scope"
          >
            <div
              className="flex rounded-lg border border-border-subtle p-0.5"
              data-testid="your-budget-scope-toggle"
            >
              {BUDGET_SCOPES.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setScope(option.value)}
                  aria-pressed={option.value === scope}
                  className={cn(
                    "flex-1 whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium",
                    option.value === scope
                      ? "bg-tertiary text-foreground"
                      : "text-muted hover:text-foreground",
                  )}
                >
                  {t(option.label)}
                </button>
              ))}
            </div>
            <div className="grid grid-cols-2 divide-x divide-border-subtle">
              <div className="pr-4">
                <StatFigure
                  testId="your-budget-allocation"
                  plainLabel
                  label={t(I18nKey.SETTINGS$YOUR_BUDGET_ALLOCATION)}
                  help={
                    scope === "user"
                      ? allocationNote
                      : t(I18nKey.SETTINGS$YOUR_BUDGET_ORG_ALLOCATION_HELP)
                  }
                  value={
                    scoped.limit === null
                      ? t(I18nKey.SETTINGS$YOUR_BUDGET_NO_LIMIT)
                      : formatCost(scoped.limit)
                  }
                />
              </div>
              <div className="pl-4">
                <StatFigure
                  testId="your-budget-remaining"
                  plainLabel
                  label={t(I18nKey.SETTINGS$YOUR_BUDGET_REMAINING)}
                  help={
                    scope === "user"
                      ? (remainingNote ??
                        t(I18nKey.SETTINGS$YOUR_BUDGET_REMAINING_HELP))
                      : t(I18nKey.SETTINGS$YOUR_BUDGET_ORG_REMAINING_HELP)
                  }
                  value={
                    scoped.remaining === null
                      ? EMPTY_VALUE
                      : formatCost(scoped.remaining)
                  }
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      {scoped.limit !== null && (
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
  const [hoveredDate, setHoveredDate] = React.useState<string | null>(null);
  const maxCost = Math.max(...days.map((day) => day.cost), 0);
  const labelEvery = Math.ceil(days.length / 7);

  return (
    // Cleared on the chart rather than per bar so the tooltip does not flicker
    // while the pointer crosses the gaps between bars.
    <div
      className={cn(
        "flex h-40 items-end",
        days.length > 31 ? "gap-px" : "gap-1",
      )}
      data-testid="your-budget-daily-chart"
      onMouseLeave={() => setHoveredDate(null)}
    >
      {days.map((day, index) => (
        <div
          key={day.date}
          className="flex h-full min-w-0 flex-1 flex-col justify-end gap-1"
          data-testid="your-budget-daily-bar"
          onMouseEnter={() => setHoveredDate(day.date)}
        >
          <div
            className="relative min-h-[2px] w-full rounded-t bg-primary"
            style={{
              height: maxCost > 0 ? `${(day.cost / maxCost) * 100}%` : 0,
            }}
          >
            {hoveredDate === day.date && (
              <div
                className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-2 -translate-x-1/2 whitespace-nowrap rounded-lg border border-border-subtle bg-base-secondary px-3 py-2 shadow-lg"
                data-testid="your-budget-daily-tooltip"
              >
                <div className="text-sm font-medium text-foreground">
                  {formatShortDate(day.date)}
                </div>
                <div className="mt-0.5 text-xs tabular-nums text-muted">
                  {formatCost(day.cost)}
                </div>
              </div>
            )}
          </div>
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
  const [hoveredModel, setHoveredModel] = React.useState<string | null>(null);
  const total = models.reduce((sum, model) => sum + model.total_cost, 0);

  return (
    // Cleared on the list rather than per row so the tooltip does not flicker
    // while the pointer crosses the gaps between rows.
    <ul
      className="flex flex-col gap-3"
      data-testid="your-budget-models"
      onMouseLeave={() => setHoveredModel(null)}
    >
      {models.map((model, index) => {
        const color = AGENT_COLORS[index % AGENT_COLORS.length];
        const percent = total > 0 ? (model.total_cost / total) * 100 : 0;
        return (
          <li
            key={model.model_name}
            className="relative flex items-center gap-3"
            onMouseEnter={() => setHoveredModel(model.model_name)}
          >
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
                  style={{ backgroundColor: color, width: `${percent}%` }}
                />
              </div>
            </div>
            <span className="shrink-0 text-sm font-medium text-foreground">
              {formatCost(model.total_cost)}
            </span>
            {hoveredModel === model.model_name && (
              <div
                className="pointer-events-none absolute bottom-full right-0 z-10 mb-2 w-max max-w-xs rounded-lg border border-border-subtle bg-base-secondary px-3 py-2 shadow-lg"
                data-testid="your-budget-model-tooltip"
              >
                <div className="break-all text-sm font-medium text-foreground">
                  {model.model_name}
                </div>
                <div className="mt-0.5 text-xs tabular-nums text-muted">
                  {`${formatCost(model.total_cost)} · ${percent.toFixed(1)}%`}
                </div>
              </div>
            )}
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
    content = (
      <>
        <BudgetSummary budget={budget} />
        <UsageBreakdown timeWindow={timeWindow} />
      </>
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
        {budget && (
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
