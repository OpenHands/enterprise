/* eslint-disable i18next/no-literal-string */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown } from "lucide-react";
import {
  ConversationsTab,
  ModelsTab,
  OverviewTab,
  UsersTab,
  type ConversationRow,
} from "#/components/features/admin-dashboard/usage-dashboard-tabs";
import {
  AGENT_COLORS,
  TIME_WINDOWS,
  formatCost,
  rowsToCsv,
} from "#/components/features/admin-dashboard/usage-dashboard-utils";
import { useClickOutsideElement } from "#/hooks/use-click-outside-element";
import { useSuperAdminUsage } from "#/hooks/query/use-super-admin-usage";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";
import { formControlFilterTriggerClassName } from "#/utils/form-control-classes";

const TABS = ["overview", "users", "models", "conversations"] as const;
type TabType = (typeof TABS)[number];

function selectedOrgLabel(
  selectedIds: string[],
  orgs: { id: string; name: string }[],
  translate: (key: I18nKey, options?: { count: number }) => string,
) {
  if (selectedIds.length === 0) {
    return translate(I18nKey.SUPER_ADMIN$ALL_ORGS);
  }
  if (selectedIds.length === 1) {
    return (
      orgs.find((org) => org.id === selectedIds[0])?.name ??
      translate(I18nKey.SUPER_ADMIN$ALL_ORGS)
    );
  }
  return translate(I18nKey.SUPER_ADMIN$COMPARE_N_ORGS, {
    count: selectedIds.length,
  });
}

function OrgComparePicker({
  orgs,
  selectedIds,
  onChange,
}: {
  orgs: { id: string; name: string }[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const ref = useClickOutsideElement<HTMLDivElement>(() => setOpen(false));
  const comparing = selectedIds.length > 0;
  const label = selectedOrgLabel(selectedIds, orgs, t);

  const toggleOrg = (orgId: string) => {
    if (selectedIds.includes(orgId)) {
      onChange(selectedIds.filter((id) => id !== orgId));
      return;
    }
    onChange([...selectedIds, orgId]);
  };

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        data-testid="super-admin-org-compare"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((current) => !current)}
        className={cn(
          formControlFilterTriggerClassName,
          "w-36 max-w-36 justify-between gap-1.5",
        )}
      >
        <span className="min-w-0 truncate">{label}</span>
        <ChevronDown className="size-4 shrink-0 text-[var(--oh-muted)]" />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={t(I18nKey.SUPER_ADMIN$COMPARE_ORGS)}
          data-testid="super-admin-org-compare-menu"
          className="absolute left-0 top-full z-50 mt-2 w-72 rounded-xl border border-[var(--oh-border)] bg-[var(--oh-surface-raised)] p-3 shadow-lg"
        >
          <button
            type="button"
            data-testid="super-admin-org-compare-all"
            onClick={() => {
              onChange([]);
              setOpen(false);
            }}
            className={cn(
              "flex w-full items-center rounded-lg px-2 py-2 text-left text-sm",
              !comparing
                ? "bg-surface-deep text-foreground"
                : "text-muted hover:bg-interactive-hover hover:text-foreground",
            )}
          >
            {t(I18nKey.SUPER_ADMIN$ALL_ORGS)}
          </button>
          <p className="px-2 pb-1 pt-3 text-xs font-medium text-text-dim">
            {t(I18nKey.SUPER_ADMIN$COMPARE_ORGS)}
          </p>
          <div className="flex flex-col gap-1">
            {orgs.map((org) => {
              const checked = selectedIds.includes(org.id);
              return (
                <label
                  key={org.id}
                  className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-foreground hover:bg-interactive-hover"
                >
                  <input
                    type="checkbox"
                    data-testid={`super-admin-org-compare-${org.id}`}
                    checked={checked}
                    onChange={() => toggleOrg(org.id)}
                    className="size-3.5 accent-[var(--oh-color-primary)]"
                  />
                  <span className="min-w-0 truncate">{org.name}</span>
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function filterConversations(
  rows: ConversationRow[],
  {
    search,
    status,
    sortBy,
    sortOrder,
  }: {
    search: string;
    status: string;
    sortBy: string;
    sortOrder: string;
  },
) {
  const query = search.trim().toLowerCase();
  const filtered = rows.filter((row) => {
    const matchesSearch =
      query.length === 0 ||
      (row.title ?? "").toLowerCase().includes(query) ||
      (row.user_email ?? "").toLowerCase().includes(query) ||
      (row.org_name ?? "").toLowerCase().includes(query);
    const matchesStatus =
      status.length === 0 ||
      (row.execution_status ?? "").toLowerCase() === status.toLowerCase();
    return matchesSearch && matchesStatus;
  });

  const direction = sortOrder === "asc" ? 1 : -1;
  return [...filtered].sort((left, right) => {
    if (sortBy === "accumulated_cost") {
      return (left.accumulated_cost - right.accumulated_cost) * direction;
    }
    if (sortBy === "total_tokens") {
      return (left.total_tokens - right.total_tokens) * direction;
    }
    if (sortBy === "created_at") {
      return (
        ((left.created_at ?? "").localeCompare(right.created_at ?? "") || 0) *
        direction
      );
    }
    return (
      ((left.updated_at ?? "").localeCompare(right.updated_at ?? "") || 0) *
      direction
    );
  });
}

export function SuperAdminDashboard() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabType>("overview");
  const [timeWindow, setTimeWindow] = useState("30d");
  const [selectedOrgIds, setSelectedOrgIds] = useState<string[]>([]);
  const [modelSearch, setModelSearch] = useState("");
  const [conversationSearch, setConversationSearch] = useState("");
  const [conversationStatus, setConversationStatus] = useState("running");
  const [conversationSortBy, setConversationSortBy] = useState("updated_at");
  const [conversationSortOrder, setConversationSortOrder] = useState("desc");
  const [conversationSandboxStatus, setConversationSandboxStatus] =
    useState("");
  const [conversationPage, setConversationPage] = useState(1);
  const [conversationPerPage, setConversationPerPage] = useState(20);
  const [stoppingIds, setStoppingIds] = useState<Set<string>>(new Set());
  const [stoppedIds, setStoppedIds] = useState<Set<string>>(new Set());
  const [pendingStop, setPendingStop] = useState<{
    id: string;
    title: string | null;
  } | null>(null);

  const {
    orgs,
    usage,
    isLoading: usageLoading,
    isError: usageError,
  } = useSuperAdminUsage({ selectedOrgIds, timeWindow });
  const comparing = selectedOrgIds.length > 0;
  const timeWindowLabel =
    timeWindow === "ytd" ? "YTD" : timeWindow.toUpperCase();
  const usageConversations = usage.conversations;
  const { activeConversations } = usage;
  const avgCostPerConversation =
    usageConversations > 0 ? usage.spend / usageConversations : 0;
  const totalSpend = formatCost(usage.spend);

  const chartData = useMemo(
    () =>
      usage.dailyUsage.map((point) => ({
        date: point.date,
        value: point.conversations,
      })),
    [usage.dailyUsage],
  );

  const compareSeries = useMemo(
    () =>
      usage.snapshots.map((snapshot, index) => ({
        id: snapshot.orgId,
        label: snapshot.orgName,
        color: AGENT_COLORS[index % AGENT_COLORS.length],
        values: snapshot.dailyUsage.map((point) => point.conversations),
      })),
    [usage.snapshots],
  );

  const agentSpendRows = useMemo(() => {
    const total = usage.agentUsage.reduce(
      (sum, row) => sum + row.total_cost,
      0,
    );
    return usage.agentUsage.map((row, index) => ({
      ...row,
      percent: total > 0 ? (row.total_cost / total) * 100 : 0,
      color: AGENT_COLORS[index % AGENT_COLORS.length],
    }));
  }, [usage.agentUsage]);
  const agentSpendTotal = agentSpendRows.reduce(
    (sum, row) => sum + row.total_cost,
    0,
  );

  const modelRows = useMemo(
    () =>
      usage.modelUsage.map((model) => {
        const avgTokens =
          model.conversation_count > 0
            ? Math.round(model.total_tokens / model.conversation_count)
            : 0;
        const avgCost =
          model.conversation_count > 0
            ? model.total_cost / model.conversation_count
            : 0;
        return { ...model, avgTokens, avgCost };
      }),
    [usage.modelUsage],
  );
  const filteredModels = useMemo(
    () =>
      modelRows.filter((model) =>
        model.model_name.toLowerCase().includes(modelSearch.toLowerCase()),
      ),
    [modelRows, modelSearch],
  );

  const conversationRows = useMemo(
    () =>
      usage.conversationRows
        .filter((row) => !stoppedIds.has(row.id))
        .map((row) =>
          stoppingIds.has(row.id) ? { ...row, execution_status: "idle" } : row,
        ),
    [stoppingIds, stoppedIds, usage.conversationRows],
  );

  const filteredConversations = useMemo(
    () =>
      filterConversations(conversationRows, {
        search: conversationSearch,
        status: conversationStatus,
        sortBy: conversationSortBy,
        sortOrder: conversationSortOrder,
      }),
    [
      conversationRows,
      conversationSearch,
      conversationSortBy,
      conversationSortOrder,
      conversationStatus,
    ],
  );

  const conversationTotalItems = filteredConversations.length;
  const conversationTotalPages = Math.max(
    1,
    Math.ceil(conversationTotalItems / conversationPerPage),
  );
  const pagedConversations = filteredConversations.slice(
    (conversationPage - 1) * conversationPerPage,
    conversationPage * conversationPerPage,
  );

  const exportUrl = useMemo(() => {
    const csv = rowsToCsv(
      [
        "organization",
        "user_email",
        "title",
        "tokens",
        "spend",
        "status",
        "started",
        "updated",
      ],
      filteredConversations.map((row) => [
        row.org_name ?? "",
        row.user_email ?? "",
        row.title ?? "",
        row.total_tokens,
        row.accumulated_cost,
        row.execution_status ?? "",
        row.created_at ?? "",
        row.updated_at ?? "",
      ]),
    );
    return URL.createObjectURL(
      new Blob([csv], { type: "text/csv;charset=utf-8;" }),
    );
  }, [filteredConversations]);

  const tabCounts = {
    overview: null,
    users: usage.users.length,
    models: modelRows.length,
    conversations: conversationTotalItems,
  };

  const pendingStopLabel = pendingStop?.title?.trim();
  const stopConfirmationText = pendingStopLabel
    ? `Stop "${pendingStopLabel}"? This will cancel any in-progress agent run.`
    : "Stop this conversation? This will cancel any in-progress agent run.";

  const confirmStop = () => {
    if (!pendingStop) return;
    const conversationId = pendingStop.id;
    setPendingStop(null);
    setStoppingIds((current) => new Set(current).add(conversationId));
    window.setTimeout(() => {
      setStoppingIds((current) => {
        const next = new Set(current);
        next.delete(conversationId);
        return next;
      });
      setStoppedIds((current) => new Set(current).add(conversationId));
    }, 400);
  };

  const scopeLabel = selectedOrgLabel(selectedOrgIds, orgs, t);

  return (
    <div className="space-y-6" data-testid="super-admin-dashboard">
      <div className="flex flex-nowrap items-center gap-x-3">
        <div className="flex min-w-0 flex-1 flex-nowrap items-center gap-4 overflow-x-auto">
          {TABS.map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={`flex shrink-0 items-center gap-1.5 px-1 py-3 text-sm font-medium transition-colors border-b-2 ${
                activeTab === tab
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted hover:text-foreground"
              }`}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
              {typeof tabCounts[tab] === "number" && (
                <span className="inline-flex items-center justify-center rounded-full bg-surface-raised px-1.5 py-0.5 text-xs text-[var(--oh-muted)]">
                  {tabCounts[tab].toLocaleString()}
                </span>
              )}
            </button>
          ))}
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <OrgComparePicker
            orgs={orgs}
            selectedIds={selectedOrgIds}
            onChange={(ids) => {
              setSelectedOrgIds(ids);
              setConversationPage(1);
            }}
          />
          <div className="flex items-center gap-0.5 rounded-lg border border-border-subtle bg-base-secondary p-1">
            {TIME_WINDOWS.map((windowOption) => (
              <button
                key={windowOption.value}
                type="button"
                onClick={() => {
                  setTimeWindow(windowOption.value);
                  setConversationPage(1);
                }}
                className={`px-2 py-1.5 text-sm rounded-md transition-colors ${
                  timeWindow === windowOption.value
                    ? "bg-surface-deep text-foreground"
                    : "text-muted hover:text-foreground"
                }`}
              >
                {windowOption.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {usageLoading ? (
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$LOADING)}
        </p>
      ) : null}
      {usageError ? (
        <p className="text-sm text-red-400">
          {t(I18nKey.SUPER_ADMIN$USAGE_LOAD_ERROR)}
        </p>
      ) : null}
      {!usageLoading && !usageError && usage.snapshots.length === 0 ? (
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$NO_USAGE_YET)}
        </p>
      ) : null}

      {activeTab === "overview" && (
        <OverviewTab
          usageConversations={usageConversations}
          activeConversations={activeConversations}
          avgCostPerConversation={avgCostPerConversation}
          totalSpend={totalSpend}
          timeWindowLabel={timeWindowLabel}
          chartData={chartData}
          chartSubtitle={`${timeWindowLabel} · ${scopeLabel}`}
          compareSeries={
            comparing && usage.snapshots.length > 1 ? compareSeries : undefined
          }
          agentSpendRows={agentSpendRows}
          agentSpendTotal={agentSpendTotal}
        />
      )}

      {activeTab === "users" && (
        <UsersTab userUsage={{ items: usage.users }} userUsageLoading={false} />
      )}

      {activeTab === "models" && (
        <ModelsTab
          modelSearch={modelSearch}
          onModelSearchChange={setModelSearch}
          filteredModels={filteredModels}
        />
      )}

      {activeTab === "conversations" && (
        <ConversationsTab
          conversationSearch={conversationSearch}
          conversationStatus={conversationStatus}
          conversationSortBy={conversationSortBy}
          conversationSortOrder={conversationSortOrder}
          conversationSandboxStatus={conversationSandboxStatus}
          exportUrl={exportUrl}
          conversationPage={conversationPage}
          conversationPerPage={conversationPerPage}
          conversationTotalPages={conversationTotalPages}
          conversationTotalItems={conversationTotalItems}
          conversationsLoading={false}
          conversationsData={{
            items: pagedConversations,
            total_pages: conversationTotalPages,
            total_items: conversationTotalItems,
          }}
          stoppingIds={stoppingIds}
          onSearchChange={(value) => {
            setConversationSearch(value);
            setConversationPage(1);
          }}
          onStatusChange={(value) => {
            setConversationStatus(value);
            setConversationPage(1);
          }}
          onSortByChange={(value) => {
            setConversationSortBy(value);
            setConversationPage(1);
          }}
          onSortOrderChange={(value) => {
            setConversationSortOrder(value);
            setConversationPage(1);
          }}
          onSandboxStatusChange={(value) => {
            setConversationSandboxStatus(value);
            setConversationPage(1);
          }}
          onPageChange={setConversationPage}
          onPerPageChange={(value) => {
            setConversationPerPage(value);
            setConversationPage(1);
          }}
          onStopConversation={(conversation) => setPendingStop(conversation)}
          pendingStop={pendingStop}
          stopConfirmationText={stopConfirmationText}
          onConfirmStop={confirmStop}
          onCancelStop={() => setPendingStop(null)}
        />
      )}
    </div>
  );
}
