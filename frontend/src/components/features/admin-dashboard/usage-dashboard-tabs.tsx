/* eslint-disable i18next/no-literal-string */
import React from "react";
import { ConfirmationModal } from "#/components/shared/modals/confirmation-modal";
import {
  ExportIcon,
  FilterIcon,
  SearchIcon,
  StopIcon,
} from "#/components/shared/icons/inline-icons";
import { useClickOutsideElement } from "#/hooks/use-click-outside-element";
import { AreaChart, KPICard, PieChart } from "./usage-dashboard-widgets";
import {
  buildExportFilename,
  formatAgentLabel,
  formatAssociatedPr,
  formatBudget,
  formatCost,
  formatDateTimeOrDash,
  formatDuration,
  formatMergedStatus,
  formatTokens,
  rowsToCsv,
} from "./usage-dashboard-utils";
import { cn, downloadBlob } from "#/utils/utils";
import {
  formControlFilterTriggerClassName,
  formControlInlineInputClassName,
  formControlNativeSelectClassName,
  formControlShellClassName,
} from "#/utils/form-control-classes";
import {
  settingsListContainerClassName,
  settingsListTableCellClassName,
  settingsListTableHeadClassName,
  settingsListTableHeaderCellClassName,
  settingsListTableRowClassName,
} from "#/utils/settings-list-classes";

const usageNativeSelectClassName = cn(
  formControlNativeSelectClassName,
  "w-auto shrink-0",
);

const usageFilterSelectClassName = formControlNativeSelectClassName;

const usageTableShellClassName = cn(settingsListContainerClassName, "min-w-0");

const usageTableHeaderCellClassName = settingsListTableHeaderCellClassName;

const usageTableHeaderCellRightClassName = cn(
  settingsListTableHeaderCellClassName,
  "text-right",
);

const usageTableCellClassName = settingsListTableCellClassName;

const usageTableCellRightClassName = cn(
  settingsListTableCellClassName,
  "text-right font-mono tabular-nums",
);

const usageTableEmptyCellClassName = cn(
  settingsListTableCellClassName,
  "h-auto py-8 text-center text-muted",
);
const DEFAULT_CONVERSATION_STATUS = "running";
const DEFAULT_CONVERSATION_SORT_BY = "updated_at";
const DEFAULT_CONVERSATION_SORT_ORDER = "desc";
const DEFAULT_CONVERSATION_SANDBOX_STATUS = "";

function countActiveConversationFilters({
  conversationStatus,
  conversationSortBy,
  conversationSortOrder,
  conversationSandboxStatus,
}: {
  conversationStatus: string;
  conversationSortBy: string;
  conversationSortOrder: string;
  conversationSandboxStatus: string;
}) {
  return [
    conversationStatus !== DEFAULT_CONVERSATION_STATUS,
    conversationSortBy !== DEFAULT_CONVERSATION_SORT_BY,
    conversationSortOrder !== DEFAULT_CONVERSATION_SORT_ORDER,
    conversationSandboxStatus !== DEFAULT_CONVERSATION_SANDBOX_STATUS,
  ].filter(Boolean).length;
}

export type ChartPoint = { date: string; value: number };

export type AgentSpendRow = {
  agent_name: string;
  total_cost: number;
  percent: number;
  color: string;
};

export function OverviewTab({
  usageConversations,
  activeConversations,
  avgCostPerConversation,
  totalSpend,
  timeWindowLabel,
  chartData,
  agentSpendRows,
  agentSpendTotal,
}: {
  usageConversations: number;
  activeConversations: number;
  avgCostPerConversation: number;
  totalSpend: string;
  timeWindowLabel: string;
  chartData: ChartPoint[];
  agentSpendRows: AgentSpendRow[];
  agentSpendTotal: number;
}) {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-4 gap-4">
        <KPICard
          label="Conversations With Usage"
          value={usageConversations.toLocaleString()}
        />
        <KPICard
          label="Active Conversations"
          value={activeConversations.toLocaleString()}
        />
        <KPICard
          label="Avg Cost / Conversation"
          value={`$${avgCostPerConversation.toFixed(2)}`}
        />
        <KPICard
          label={`Total Spend (${timeWindowLabel})`}
          value={totalSpend}
        />
      </div>

      <div className="grid grid-cols-1 items-stretch gap-6 lg:grid-cols-3">
        <div className="flex h-full flex-col rounded-lg border border-border-subtle bg-base-secondary p-6 lg:col-span-2">
          <div className="mb-4 flex items-start justify-between">
            <div>
              <h2 className="text-lg font-medium text-foreground">
                Conversations started per day
              </h2>
              <p className="text-sm text-muted">
                {timeWindowLabel} · all users
              </p>
            </div>
            <button
              type="button"
              disabled={chartData.length === 0}
              onClick={() => {
                const csv = rowsToCsv(
                  ["date", "conversations_started"],
                  chartData.map((point) => [point.date, point.value]),
                );
                downloadBlob(
                  new Blob([csv], { type: "text/csv;charset=utf-8;" }),
                  buildExportFilename("conversations_per_day"),
                );
              }}
              className={cn(
                formControlFilterTriggerClassName,
                "text-[var(--oh-muted)] hover:text-white disabled:opacity-50",
              )}
            >
              <ExportIcon />
              Export CSV
            </button>
          </div>
          <div className="relative min-h-36 flex-1">
            {chartData.length > 0 ? (
              <div className="absolute inset-0">
                <AreaChart data={chartData} />
              </div>
            ) : (
              <div className="flex h-full min-h-36 items-center justify-center py-8 text-center text-sm text-muted">
                No usage data available yet.
              </div>
            )}
          </div>
        </div>
        <div className="flex h-full flex-col rounded-lg border border-border-subtle bg-base-secondary p-6">
          <div className="mb-4">
            <h2 className="text-lg font-medium text-foreground">
              Spend by agent
            </h2>
            <p className="text-sm text-muted">
              {timeWindowLabel} · total spend
            </p>
          </div>
          {agentSpendTotal > 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-4">
              <PieChart
                data={agentSpendRows.map((row) => ({
                  value: row.total_cost,
                  color: row.color,
                  label: row.agent_name,
                  percent: row.percent,
                }))}
                total={agentSpendTotal}
              />
              <div className="w-full min-w-0 space-y-2">
                {agentSpendRows.map((row) => (
                  <div key={row.agent_name} className="space-y-0.5 text-sm">
                    <div className="flex min-w-0 items-center gap-2">
                      <span
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{ backgroundColor: row.color }}
                      />
                      <span className="min-w-0 flex-1 truncate text-foreground">
                        {row.agent_name}
                      </span>
                    </div>
                    <div className="pl-4 tabular-nums text-muted">
                      {formatCost(row.total_cost)} · {row.percent.toFixed(1)}%
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex flex-1 items-center justify-center py-8 text-center text-sm text-muted">
              No agent spend data available yet.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export type ConversationRow = {
  id: string;
  user_email?: string | null;
  total_tokens: number;
  accumulated_cost: number;
  created_at?: string | null;
  updated_at?: string | null;
  pr_number?: number[];
  selected_repository?: string | null;
  pr_merged?: boolean | null;
  agent_kind?: string | null;
  llm_model?: string | null;
  trigger?: string | null;
  execution_status?: string | null;
  title?: string | null;
};

export type ConversationsResponse = {
  items: ConversationRow[];
  total_pages?: number;
  total_items?: number;
};

export function ConversationsTab({
  conversationSearch,
  conversationStatus,
  conversationSortBy,
  conversationSortOrder,
  conversationSandboxStatus,
  exportUrl,
  conversationPage,
  conversationPerPage,
  conversationTotalPages,
  conversationTotalItems,
  conversationsLoading,
  conversationsData,
  stoppingIds,
  onSearchChange,
  onStatusChange,
  onSortByChange,
  onSortOrderChange,
  onSandboxStatusChange,
  onPageChange,
  onPerPageChange,
  onStopConversation,
  pendingStop,
  stopConfirmationText,
  onConfirmStop,
  onCancelStop,
}: {
  conversationSearch: string;
  conversationStatus: string;
  conversationSortBy: string;
  conversationSortOrder: string;
  conversationSandboxStatus: string;
  exportUrl: string;
  conversationPage: number;
  conversationPerPage: number;
  conversationTotalPages: number;
  conversationTotalItems: number;
  conversationsLoading: boolean;
  conversationsData?: ConversationsResponse;
  stoppingIds: Set<string>;
  onSearchChange: (value: string) => void;
  onStatusChange: (value: string) => void;
  onSortByChange: (value: string) => void;
  onSortOrderChange: (value: string) => void;
  onSandboxStatusChange: (value: string) => void;
  onPageChange: (page: number) => void;
  onPerPageChange: (value: number) => void;
  onStopConversation: (conversation: {
    id: string;
    title: string | null;
  }) => void;
  pendingStop: { id: string; title: string | null } | null;
  stopConfirmationText: string;
  onConfirmStop: () => void;
  onCancelStop: () => void;
}) {
  const [isFiltersOpen, setIsFiltersOpen] = React.useState(false);
  const filtersRef = useClickOutsideElement<HTMLDivElement>(() =>
    setIsFiltersOpen(false),
  );
  const activeFilterCount = countActiveConversationFilters({
    conversationStatus,
    conversationSortBy,
    conversationSortOrder,
    conversationSandboxStatus,
  });

  const clearFilters = () => {
    onStatusChange(DEFAULT_CONVERSATION_STATUS);
    onSortByChange(DEFAULT_CONVERSATION_SORT_BY);
    onSortOrderChange(DEFAULT_CONVERSATION_SORT_ORDER);
    onSandboxStatusChange(DEFAULT_CONVERSATION_SANDBOX_STATUS);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className={cn(formControlShellClassName, "min-w-0 flex-1")}>
          <span className="ml-3 shrink-0 text-tertiary-alt" aria-hidden>
            <SearchIcon />
          </span>
          <input
            type="text"
            placeholder="Search by title or user..."
            value={conversationSearch}
            onChange={(event) => onSearchChange(event.target.value)}
            className={cn(formControlInlineInputClassName, "text-white")}
          />
        </div>

        <div ref={filtersRef} className="relative shrink-0">
          <button
            type="button"
            data-testid="conversation-filters-button"
            onClick={() => setIsFiltersOpen((open) => !open)}
            aria-expanded={isFiltersOpen}
            aria-haspopup="dialog"
            className={cn(
              formControlFilterTriggerClassName,
              "text-white",
              isFiltersOpen && "bg-surface-raised",
            )}
          >
            <FilterIcon />
            Filters
            {activeFilterCount > 0 && (
              <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-white px-1.5 py-0.5 text-[11px] font-medium leading-none text-black">
                {activeFilterCount}
              </span>
            )}
          </button>

          {isFiltersOpen && (
            <div
              role="dialog"
              aria-label="Conversation filters"
              data-testid="conversation-filters-panel"
              className={cn(
                "absolute right-0 top-full z-50 mt-2 w-72",
                "rounded-xl border border-[var(--oh-border)] bg-[var(--oh-surface-raised)] p-4 shadow-lg",
              )}
            >
              <div className="flex flex-col gap-3">
                <label className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-text-dim">
                    Status
                  </span>
                  <select
                    value={conversationStatus}
                    onChange={(event) => onStatusChange(event.target.value)}
                    className={usageFilterSelectClassName}
                  >
                    <option value="">All statuses</option>
                    <option value="running">Running</option>
                    <option value="idle">Idle</option>
                    <option value="paused">Paused</option>
                    <option value="finished">Finished</option>
                    <option value="error">Error</option>
                    <option value="stuck">Stuck</option>
                  </select>
                </label>

                <label className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-text-dim">
                    Sort by
                  </span>
                  <select
                    value={conversationSortBy}
                    onChange={(event) => onSortByChange(event.target.value)}
                    className={usageFilterSelectClassName}
                  >
                    <option value="updated_at">Last updated</option>
                    <option value="created_at">Created</option>
                    <option value="title">Title</option>
                    <option value="llm_model">Model</option>
                    <option value="accumulated_cost">Cost</option>
                  </select>
                </label>

                <label className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-text-dim">
                    Order
                  </span>
                  <select
                    value={conversationSortOrder}
                    onChange={(event) => onSortOrderChange(event.target.value)}
                    className={usageFilterSelectClassName}
                  >
                    <option value="desc">Descending</option>
                    <option value="asc">Ascending</option>
                  </select>
                </label>

                <label className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-text-dim">
                    Runtime status
                  </span>
                  <select
                    value={conversationSandboxStatus}
                    onChange={(event) =>
                      onSandboxStatusChange(event.target.value)
                    }
                    className={usageFilterSelectClassName}
                  >
                    <option value="">All</option>
                    <option value="RUNNING">Running</option>
                    <option value="STARTING">Starting</option>
                    <option value="PAUSED">Paused</option>
                    <option value="ERROR">Error</option>
                    <option value="MISSING">Missing</option>
                  </select>
                </label>

                {activeFilterCount > 0 && (
                  <button
                    type="button"
                    onClick={clearFilters}
                    className="self-start text-sm text-[var(--oh-muted)] transition-colors hover:text-white cursor-pointer"
                  >
                    Reset filters
                  </button>
                )}
              </div>
            </div>
          )}
        </div>

        <a
          href={exportUrl}
          className={cn(
            formControlFilterTriggerClassName,
            "text-[var(--oh-muted)] hover:text-white",
          )}
        >
          <ExportIcon />
          Export CSV
        </a>
      </div>

      <div className={usageTableShellClassName}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-max">
            <thead className={settingsListTableHeadClassName}>
              <tr>
                <th className={usageTableHeaderCellClassName}>User</th>
                <th className={usageTableHeaderCellRightClassName}>Tokens</th>
                <th className={usageTableHeaderCellRightClassName}>Spend</th>
                <th className={usageTableHeaderCellClassName}>Duration</th>
                <th className={usageTableHeaderCellClassName}>Started</th>
                <th className={usageTableHeaderCellClassName}>Last update</th>
                <th className={usageTableHeaderCellClassName}>Associated PR</th>
                <th className={usageTableHeaderCellClassName}>Merged?</th>
                <th className={usageTableHeaderCellClassName}>Agent</th>
                <th className={usageTableHeaderCellClassName}>Type</th>
                <th className={usageTableHeaderCellRightClassName}>Stop</th>
              </tr>
            </thead>
            <tbody>
              {conversationsLoading && (
                <tr>
                  <td colSpan={11} className={usageTableEmptyCellClassName}>
                    Loading conversations...
                  </td>
                </tr>
              )}
              {!conversationsLoading &&
                (conversationsData?.items.length ?? 0) === 0 && (
                  <tr>
                    <td colSpan={11} className={usageTableEmptyCellClassName}>
                      No conversations found for this time window.
                    </td>
                  </tr>
                )}
              {conversationsData?.items.map((conversation) => {
                const isRunning =
                  conversation.execution_status?.toLowerCase() === "running";
                return (
                  <tr
                    key={conversation.id}
                    className={settingsListTableRowClassName}
                  >
                    <td className={cn(usageTableCellClassName, "h-auto py-3")}>
                      <div className="text-foreground font-medium">
                        {conversation.user_email?.split("@")[0] || "Unknown"}
                      </div>
                      <div className="text-xs text-tertiary-alt">
                        {conversation.user_email || "-"}
                      </div>
                    </td>
                    <td
                      className={cn(
                        usageTableCellRightClassName,
                        "text-foreground",
                      )}
                    >
                      {formatTokens(conversation.total_tokens)}
                    </td>
                    <td
                      className={cn(
                        usageTableCellRightClassName,
                        "text-foreground",
                      )}
                    >
                      {formatCost(conversation.accumulated_cost)}
                    </td>
                    <td className={cn(usageTableCellClassName, "text-muted")}>
                      {formatDuration(
                        conversation.created_at,
                        conversation.updated_at,
                      )}
                    </td>
                    <td className={cn(usageTableCellClassName, "text-muted")}>
                      {formatDateTimeOrDash(conversation.created_at)}
                    </td>
                    <td className={cn(usageTableCellClassName, "text-muted")}>
                      {formatDateTimeOrDash(conversation.updated_at)}
                    </td>
                    <td className={cn(usageTableCellClassName, "text-muted")}>
                      {formatAssociatedPr(conversation)}
                    </td>
                    <td className={cn(usageTableCellClassName, "text-muted")}>
                      {formatMergedStatus(conversation.pr_merged)}
                    </td>
                    <td className={cn(usageTableCellClassName, "text-muted")}>
                      {formatAgentLabel(conversation)}
                    </td>
                    <td
                      className={cn(
                        usageTableCellClassName,
                        "text-muted capitalize",
                      )}
                    >
                      {conversation.trigger || "-"}
                    </td>
                    <td className={cn(usageTableCellClassName, "text-right")}>
                      {isRunning && (
                        <button
                          type="button"
                          onClick={() =>
                            onStopConversation({
                              id: conversation.id,
                              title: conversation.title ?? null,
                            })
                          }
                          disabled={stoppingIds.has(conversation.id)}
                          className="inline-flex items-center gap-1.5 rounded px-2 py-1 text-xs text-muted transition-colors hover:bg-interactive-hover hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted"
                          title="Stop conversation"
                          aria-label="Stop conversation"
                        >
                          <StopIcon />
                          {stoppingIds.has(conversation.id)
                            ? "Stopping…"
                            : "Stop"}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-[var(--oh-border)] px-3 py-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onPageChange(Math.max(1, conversationPage - 1))}
              disabled={conversationPage <= 1}
              className={`flex items-center gap-1 px-2 py-1 text-sm rounded transition-colors ${
                conversationPage <= 1
                  ? "text-text-dim cursor-not-allowed opacity-60"
                  : "text-muted hover:text-foreground hover:bg-interactive-hover"
              }`}
            >
              Previous
            </button>
            <button
              type="button"
              onClick={() =>
                onPageChange(
                  Math.min(conversationTotalPages, conversationPage + 1),
                )
              }
              disabled={conversationPage >= conversationTotalPages}
              className={`flex items-center gap-1 px-2 py-1 text-sm rounded transition-colors ${
                conversationPage >= conversationTotalPages
                  ? "text-text-dim cursor-not-allowed opacity-60"
                  : "text-muted hover:text-foreground hover:bg-interactive-hover"
              }`}
            >
              Next
            </button>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-muted text-sm">Per page</span>
              <select
                value={conversationPerPage}
                onChange={(event) =>
                  onPerPageChange(Number(event.target.value))
                }
                className={cn(usageNativeSelectClassName, "min-w-16")}
              >
                <option value="10">10</option>
                <option value="20">20</option>
                <option value="50">50</option>
              </select>
            </div>
            <span className="text-muted text-sm">
              Page {conversationPage} of {conversationTotalPages} ·{" "}
              {conversationTotalItems} conversations
            </span>
          </div>
        </div>
      </div>

      {pendingStop && (
        <ConfirmationModal
          text={stopConfirmationText}
          onConfirm={onConfirmStop}
          onCancel={onCancelStop}
        />
      )}
    </div>
  );
}

export type UserUsageRow = {
  user_id: string;
  user_name?: string | null;
  user_email?: string | null;
  conversation_count: number;
  first_conversation_at?: string | null;
  last_conversation_at?: string | null;
  first_login_at?: string | null;
  last_login_at?: string | null;
  spend_mtd: number;
  spend_ytd: number;
  spend_lifetime: number;
  budget_monthly_limit?: number | null;
  budget_is_disabled?: boolean;
  prs_merged?: number | null;
};

export type UserUsageResponse = {
  items: UserUsageRow[];
};

export function UsersTab({
  userUsage,
  userUsageLoading,
}: {
  userUsage?: UserUsageResponse;
  userUsageLoading: boolean;
}) {
  return (
    <div className="space-y-4">
      <div className={usageTableShellClassName}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-max">
            <thead className={settingsListTableHeadClassName}>
              <tr>
                <th className={usageTableHeaderCellClassName}>User</th>
                <th className={usageTableHeaderCellRightClassName}>Convos</th>
                <th className={usageTableHeaderCellClassName}>First convo</th>
                <th className={usageTableHeaderCellClassName}>Last convo</th>
                <th className={usageTableHeaderCellClassName}>First login</th>
                <th className={usageTableHeaderCellClassName}>Last login</th>
                <th className={usageTableHeaderCellRightClassName}>
                  Spend MTD
                </th>
                <th className={usageTableHeaderCellRightClassName}>
                  Spend YTD
                </th>
                <th className={usageTableHeaderCellRightClassName}>Lifetime</th>
                <th className={usageTableHeaderCellClassName}>Budget</th>
                <th className={usageTableHeaderCellRightClassName}>
                  PRs merged
                </th>
              </tr>
            </thead>
            <tbody>
              {userUsageLoading && (
                <tr>
                  <td colSpan={11} className={usageTableEmptyCellClassName}>
                    Loading user usage...
                  </td>
                </tr>
              )}
              {!userUsageLoading && (userUsage?.items.length ?? 0) === 0 && (
                <tr>
                  <td colSpan={11} className={usageTableEmptyCellClassName}>
                    No user usage data available yet.
                  </td>
                </tr>
              )}
              {userUsage?.items.map((user) => (
                <tr
                  key={user.user_id}
                  className={settingsListTableRowClassName}
                >
                  <td className={cn(usageTableCellClassName, "h-auto py-3")}>
                    <div className="text-foreground font-medium">
                      {user.user_name ??
                        user.user_email?.split("@")[0] ??
                        "Unknown"}
                    </div>
                    <div className="text-xs text-tertiary-alt">
                      {user.user_email || "-"}
                    </div>
                  </td>
                  <td
                    className={cn(
                      usageTableCellRightClassName,
                      "text-foreground",
                    )}
                  >
                    {user.conversation_count.toLocaleString()}
                  </td>
                  <td className={cn(usageTableCellClassName, "text-muted")}>
                    {formatDateTimeOrDash(user.first_conversation_at)}
                  </td>
                  <td className={cn(usageTableCellClassName, "text-muted")}>
                    {formatDateTimeOrDash(user.last_conversation_at)}
                  </td>
                  <td className={cn(usageTableCellClassName, "text-muted")}>
                    {formatDateTimeOrDash(user.first_login_at)}
                  </td>
                  <td className={cn(usageTableCellClassName, "text-muted")}>
                    {formatDateTimeOrDash(user.last_login_at)}
                  </td>
                  <td
                    className={cn(
                      usageTableCellRightClassName,
                      "text-foreground",
                    )}
                  >
                    {formatCost(user.spend_mtd)}
                  </td>
                  <td
                    className={cn(
                      usageTableCellRightClassName,
                      "text-foreground",
                    )}
                  >
                    {formatCost(user.spend_ytd)}
                  </td>
                  <td
                    className={cn(
                      usageTableCellRightClassName,
                      "text-foreground",
                    )}
                  >
                    {formatCost(user.spend_lifetime)}
                  </td>
                  <td className={cn(usageTableCellClassName, "text-muted")}>
                    {formatBudget(user)}
                  </td>
                  <td
                    className={cn(usageTableCellRightClassName, "text-muted")}
                  >
                    {user.prs_merged ?? "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export type ModelUsageRow = {
  model_name: string;
  conversation_count: number;
  total_tokens: number;
  avgTokens: number;
  avgCost: number;
  total_cost: number;
};

export function ModelsTab({
  modelSearch,
  onModelSearchChange,
  filteredModels,
}: {
  modelSearch: string;
  onModelSearchChange: (value: string) => void;
  filteredModels: ModelUsageRow[];
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className={cn(formControlShellClassName, "w-64")}>
          <span className="ml-3 shrink-0 text-tertiary-alt" aria-hidden>
            <SearchIcon />
          </span>
          <input
            type="text"
            placeholder="Search models..."
            value={modelSearch}
            onChange={(event) => onModelSearchChange(event.target.value)}
            className={cn(formControlInlineInputClassName, "text-white")}
          />
        </div>
        <button
          type="button"
          disabled={filteredModels.length === 0}
          onClick={() => {
            const csv = rowsToCsv(
              [
                "model_name",
                "conversation_count",
                "total_tokens",
                "avg_tokens_per_conversation",
                "avg_cost_per_conversation",
                "total_cost",
              ],
              filteredModels.map((model) => [
                model.model_name,
                model.conversation_count,
                model.total_tokens,
                model.avgTokens,
                model.avgCost.toFixed(2),
                model.total_cost.toFixed(2),
              ]),
            );
            downloadBlob(
              new Blob([csv], { type: "text/csv;charset=utf-8;" }),
              buildExportFilename("model_usage"),
            );
          }}
          className={cn(
            formControlFilterTriggerClassName,
            "text-[var(--oh-muted)] hover:text-white disabled:opacity-50",
          )}
        >
          <ExportIcon />
          Export CSV
        </button>
      </div>

      <div className={usageTableShellClassName}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-max">
            <thead className={settingsListTableHeadClassName}>
              <tr>
                <th className={usageTableHeaderCellClassName}>Model</th>
                <th className={usageTableHeaderCellRightClassName}>
                  Conversations
                </th>
                <th className={usageTableHeaderCellRightClassName}>
                  Tokens Used
                </th>
                <th className={usageTableHeaderCellRightClassName}>
                  Avg Tokens / Convo
                </th>
                <th className={usageTableHeaderCellRightClassName}>
                  Avg Cost / Convo
                </th>
                <th className={usageTableHeaderCellRightClassName}>
                  Total Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {filteredModels.map((model) => (
                <tr
                  key={model.model_name}
                  className={settingsListTableRowClassName}
                >
                  <td className={usageTableCellClassName}>
                    <div className="truncate font-medium text-foreground">
                      {model.model_name}
                    </div>
                  </td>
                  <td
                    className={cn(
                      usageTableCellRightClassName,
                      "text-foreground",
                    )}
                  >
                    {model.conversation_count.toLocaleString()}
                  </td>
                  <td
                    className={cn(
                      usageTableCellRightClassName,
                      "text-foreground",
                    )}
                  >
                    {formatTokens(model.total_tokens)}
                  </td>
                  <td
                    className={cn(
                      usageTableCellRightClassName,
                      "text-foreground",
                    )}
                  >
                    {formatTokens(model.avgTokens)}
                  </td>
                  <td
                    className={cn(
                      usageTableCellRightClassName,
                      "text-foreground",
                    )}
                  >
                    ${model.avgCost.toFixed(2)}
                  </td>
                  <td
                    className={cn(
                      usageTableCellRightClassName,
                      "font-medium text-foreground",
                    )}
                  >
                    ${model.total_cost.toFixed(2)}
                  </td>
                </tr>
              ))}

              {filteredModels.length === 0 && (
                <tr>
                  <td colSpan={6} className={usageTableEmptyCellClassName}>
                    No model usage data available for this time window.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
