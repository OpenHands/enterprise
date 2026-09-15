import { useMemo, useState, type ReactNode } from "react";
import { Check, MessageSquareShare, X } from "lucide-react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { StyledTooltip } from "#/components/shared/buttons/styled-tooltip";
import { ApproveSelectedModal } from "#/components/features/integrations-hub/approve-selected-modal";
import { ApprovalsDecisionMenu } from "#/components/features/integrations-hub/approvals-decision-menu";
import { HubFilterTabs } from "#/components/features/integrations-hub/hub-filter-tabs";
import { HubSearchField } from "#/components/features/integrations-hub/hub-search-field";
import { PERSONAL_INTEGRATIONS_PATHS } from "#/components/features/integrations-hub/integrations-hub-paths";
import { IntegrationsHubPageHeader } from "#/components/features/integrations-hub/integrations-hub-page-header";
import { hubSearchEmptyStateClassName } from "#/components/features/integrations-hub/hub-card-classes";
import {
  formatHubDurationHours,
  formatHubTimestamp,
} from "#/components/features/integrations-hub/hub-format";
import { HubTruncatedText } from "#/components/features/integrations-hub/hub-truncated-text";
import { IntegrationProviderIcon } from "#/components/features/settings/git-settings/integration-provider-icon";
import { useIntegrationsHubStub } from "#/hooks/query/use-integrations-hub-stub";
import { I18nKey } from "#/i18n/declaration";
import type { HubApprovalStatus } from "#/types/integrations-hub";
import { formControlTransitionClassName } from "#/utils/form-control-classes";
import {
  settingsListContainerClassName,
  settingsListIconActionButtonClassName,
  settingsListRowHoverClassName,
  settingsListTableHeadClassName,
  settingsListTableHeaderCellClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";

function TimestampCell({ value }: { value?: string }) {
  const parts = formatHubTimestamp(value);
  if (!parts) {
    return <span className="text-white">—</span>;
  }
  return (
    <div className="flex flex-col whitespace-nowrap text-white">
      <span>{parts.date}</span>
      <span className="text-[var(--oh-text-secondary)]">{parts.time}</span>
    </div>
  );
}

const headerCellClassName = cn(
  settingsListTableHeaderCellClassName,
  "px-3 whitespace-nowrap text-xs font-medium text-tertiary-light",
);
const tableCellClassName =
  "min-w-0 overflow-hidden px-3 py-2 align-middle text-xs text-[var(--oh-text-secondary)]";

const COLUMN_WIDTH = {
  select: "w-8",
  tool: "w-[30%]",
  identity: "w-[22%]",
  requested: "w-[14%]",
  duration: "w-[10%]",
  decided: "w-[14%]",
  scopes: "w-[16%]",
  actions: "w-24",
} as const;

function ActionIconTooltip({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <StyledTooltip content={label} placement="top">
      {children}
    </StyledTooltip>
  );
}

function ConversationIconLink({
  conversationId,
  approvalId,
}: {
  conversationId: string;
  approvalId: string;
}) {
  const { t } = useTranslation();
  const label = t(I18nKey.INTEGRATIONS_HUB$OPEN_CONVERSATION);
  return (
    <ActionIconTooltip label={label}>
      <Link
        to={`/conversations/${conversationId}`}
        aria-label={label}
        data-testid={`approval-conversation-${approvalId}`}
        onClick={(event) => event.stopPropagation()}
        className={cn(
          settingsListIconActionButtonClassName,
          "text-[var(--oh-text-secondary)]",
        )}
      >
        <MessageSquareShare className="size-4" aria-hidden strokeWidth={2} />
      </Link>
    </ActionIconTooltip>
  );
}

export function AgentRequestsPage() {
  const { t } = useTranslation();
  const { approvals, decideApprovals } = useIntegrationsHubStub();
  const [filter, setFilter] = useState<HubApprovalStatus>("pending");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [approveIds, setApproveIds] = useState<string[] | null>(null);

  const counts = useMemo(
    () => ({
      pending: approvals.filter((item) => item.status === "pending").length,
      approved: approvals.filter((item) => item.status === "approved").length,
      denied: approvals.filter((item) => item.status === "denied").length,
    }),
    [approvals],
  );

  const query = search.trim().toLowerCase();
  const visible = useMemo(
    () =>
      approvals.filter((item) => {
        if (item.status !== filter) {
          return false;
        }
        if (!query) {
          return true;
        }
        return `${item.toolName} ${item.integrationKey} ${item.agentId}`
          .toLowerCase()
          .includes(query);
      }),
    [approvals, filter, query],
  );

  const selectedIds = visible
    .filter((item) => selected[item.id])
    .map((item) => item.id);
  const showSelection = filter === "pending";
  const showDecisionColumns = filter !== "pending";

  return (
    <div
      className="flex flex-col gap-6"
      data-testid="integrations-hub-agent-requests"
    >
      <IntegrationsHubPageHeader
        title={I18nKey.INTEGRATIONS_HUB$NAV_AGENT_REQUESTS}
        subtitle={I18nKey.INTEGRATIONS_HUB$PAGE_AGENT_REQUESTS_SUBLINE}
        subtitleExtra={
          <>
            {" "}
            {t(I18nKey.INTEGRATIONS_HUB$LONG_DURATION_PREFIX)}{" "}
            <Link
              to={PERSONAL_INTEGRATIONS_PATHS.integrations}
              className="text-white transition-opacity hover:opacity-80"
            >
              {t(I18nKey.INTEGRATIONS_HUB$LONG_DURATION_LINK)}
            </Link>
            {t(I18nKey.INTEGRATIONS_HUB$LONG_DURATION_SUFFIX)}
          </>
        }
      />
      <HubSearchField
        value={search}
        onChange={setSearch}
        placeholder={t(I18nKey.INTEGRATIONS_HUB$SEARCH_APPROVALS)}
        testId="integrations-hub-approvals-search"
      />
      <div className="flex items-end justify-between gap-4">
        <HubFilterTabs
          testId="approvals-filter"
          ariaLabel={t(I18nKey.INTEGRATIONS_HUB$FILTER_ARIA)}
          value={filter}
          onChange={(next) => {
            setFilter(next);
            setSelected({});
          }}
          options={[
            {
              value: "pending",
              label: t(I18nKey.INTEGRATIONS_HUB$FILTER_PENDING),
              count: counts.pending,
            },
            {
              value: "approved",
              label: t(I18nKey.INTEGRATIONS_HUB$FILTER_APPROVED),
              count: counts.approved,
            },
            {
              value: "denied",
              label: t(I18nKey.INTEGRATIONS_HUB$FILTER_DENIED),
              count: counts.denied,
            },
          ]}
        />
        {showSelection ? (
          <ApprovalsDecisionMenu
            canSelectAll={visible.length > 0}
            canDeselect={selectedIds.length > 0}
            canApprove={selectedIds.length > 0}
            canDeny={selectedIds.length > 0}
            onSelectAll={() =>
              setSelected(
                Object.fromEntries(visible.map((item) => [item.id, true])),
              )
            }
            onDeselect={() => setSelected({})}
            onApprove={() => setApproveIds(selectedIds)}
            onDeny={() => decideApprovals(selectedIds, "denied")}
          />
        ) : null}
      </div>

      {visible.length === 0 ? (
        <div
          data-testid="approvals-empty-state"
          className={hubSearchEmptyStateClassName}
        >
          <p className="text-xs text-tertiary-light">
            {t(I18nKey.INTEGRATIONS_HUB$APPROVALS_EMPTY_FILTER, {
              filter,
            })}
          </p>
        </div>
      ) : (
        <div
          data-testid="approvals-list"
          className={settingsListContainerClassName}
        >
          <table className="w-full table-fixed border-collapse text-left">
            <thead className={settingsListTableHeadClassName}>
              <tr>
                {showSelection ? (
                  <th
                    className={cn(
                      headerCellClassName,
                      COLUMN_WIDTH.select,
                      "pr-0",
                    )}
                  >
                    <span className="sr-only">
                      {t(I18nKey.INTEGRATIONS_HUB$COLUMN_ACTIONS)}
                    </span>
                  </th>
                ) : null}
                <th
                  className={cn(
                    headerCellClassName,
                    COLUMN_WIDTH.tool,
                    showSelection && "pl-2",
                  )}
                >
                  {t(I18nKey.INTEGRATIONS_HUB$COLUMN_TOOL)}
                </th>
                <th className={cn(headerCellClassName, COLUMN_WIDTH.identity)}>
                  {t(I18nKey.INTEGRATIONS_HUB$COLUMN_IDENTITY)}
                </th>
                <th className={cn(headerCellClassName, COLUMN_WIDTH.requested)}>
                  {t(I18nKey.INTEGRATIONS_HUB$COLUMN_REQUESTED)}
                </th>
                <th className={cn(headerCellClassName, COLUMN_WIDTH.duration)}>
                  {t(I18nKey.INTEGRATIONS_HUB$COLUMN_DURATION)}
                </th>
                {showDecisionColumns ? (
                  <>
                    <th
                      className={cn(headerCellClassName, COLUMN_WIDTH.decided)}
                    >
                      {t(I18nKey.INTEGRATIONS_HUB$COLUMN_DECIDED_BY)}
                    </th>
                    <th
                      className={cn(headerCellClassName, COLUMN_WIDTH.decided)}
                    >
                      {t(I18nKey.INTEGRATIONS_HUB$COLUMN_DECIDED_AT)}
                    </th>
                  </>
                ) : null}
                <th className={cn(headerCellClassName, COLUMN_WIDTH.scopes)}>
                  {t(I18nKey.INTEGRATIONS_HUB$COLUMN_SCOPES)}
                </th>
                <th className={cn(headerCellClassName, COLUMN_WIDTH.actions)}>
                  <span className="sr-only">
                    {t(I18nKey.INTEGRATIONS_HUB$COLUMN_ACTIONS)}
                  </span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--oh-border)]">
              {visible.map((approval) => (
                <tr
                  key={approval.id}
                  data-testid={`integrations-hub-approval-${approval.id}`}
                  className={cn(
                    "align-middle",
                    formControlTransitionClassName,
                    showSelection && settingsListRowHoverClassName,
                    showSelection && "cursor-pointer",
                    selected[approval.id] &&
                      "bg-[var(--oh-interactive-selected)]",
                  )}
                  onClick={() => {
                    if (!showSelection) {
                      return;
                    }
                    setSelected((current) => ({
                      ...current,
                      [approval.id]: !current[approval.id],
                    }));
                  }}
                >
                  {showSelection ? (
                    <td
                      className={cn(
                        tableCellClassName,
                        COLUMN_WIDTH.select,
                        "pr-0 align-middle",
                      )}
                    >
                      <input
                        type="checkbox"
                        className="m-0 block"
                        aria-label={approval.toolName}
                        checked={Boolean(selected[approval.id])}
                        onClick={(event) => event.stopPropagation()}
                        onChange={(event) =>
                          setSelected((current) => ({
                            ...current,
                            [approval.id]: event.target.checked,
                          }))
                        }
                      />
                    </td>
                  ) : null}
                  <td
                    className={cn(
                      tableCellClassName,
                      COLUMN_WIDTH.tool,
                      showSelection && "pl-2",
                    )}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <IntegrationProviderIcon
                        provider={approval.integrationKey}
                        size="sm"
                      />
                      <div className="min-w-0">
                        <HubTruncatedText
                          text={`${approval.integrationKey}.${approval.toolName}`}
                          className="font-medium text-white"
                        />
                        {approval.justification ? (
                          <HubTruncatedText
                            text={approval.justification}
                            className="mt-0.5 text-[var(--oh-text-secondary)]"
                          />
                        ) : null}
                      </div>
                    </div>
                  </td>
                  <td className={cn(tableCellClassName, COLUMN_WIDTH.identity)}>
                    <HubTruncatedText
                      text={`${approval.agentId} · ${approval.agentClass}`}
                      className="text-white"
                    />
                  </td>
                  <td
                    className={cn(tableCellClassName, COLUMN_WIDTH.requested)}
                  >
                    <TimestampCell value={approval.createdAt} />
                  </td>
                  <td
                    className={cn(
                      tableCellClassName,
                      COLUMN_WIDTH.duration,
                      "text-white",
                    )}
                  >
                    {formatHubDurationHours(approval.requestedMinutes)}
                  </td>
                  {showDecisionColumns ? (
                    <>
                      <td
                        className={cn(
                          tableCellClassName,
                          COLUMN_WIDTH.decided,
                          "text-white",
                        )}
                      >
                        {approval.decidedBy ?? "—"}
                      </td>
                      <td
                        className={cn(tableCellClassName, COLUMN_WIDTH.decided)}
                      >
                        <TimestampCell value={approval.decidedAt} />
                      </td>
                    </>
                  ) : null}
                  <td
                    className={cn(
                      tableCellClassName,
                      COLUMN_WIDTH.scopes,
                      "text-white",
                    )}
                  >
                    <HubTruncatedText
                      text={
                        approval.scopes.join(", ") ||
                        t(I18nKey.INTEGRATIONS_HUB$DEFAULT_SCOPES)
                      }
                      className="text-white"
                    />
                  </td>
                  <td className={cn(tableCellClassName, COLUMN_WIDTH.actions)}>
                    <div className="flex items-center justify-end">
                      {approval.conversationId ? (
                        <ConversationIconLink
                          approvalId={approval.id}
                          conversationId={approval.conversationId}
                        />
                      ) : null}
                      {showSelection ? (
                        <div className="ml-3 flex items-center gap-1">
                          <ActionIconTooltip
                            label={t(I18nKey.INTEGRATIONS_HUB$APPROVE)}
                          >
                            <button
                              type="button"
                              aria-label={t(I18nKey.INTEGRATIONS_HUB$APPROVE)}
                              className="inline-flex size-6 items-center justify-center rounded-full text-[var(--oh-success)] hover:bg-[color:rgba(165,231,94,0.12)]"
                              onClick={(event) => {
                                event.stopPropagation();
                                setApproveIds([approval.id]);
                              }}
                            >
                              <Check size={14} aria-hidden />
                            </button>
                          </ActionIconTooltip>
                          <ActionIconTooltip
                            label={t(I18nKey.INTEGRATIONS_HUB$DENY)}
                          >
                            <button
                              type="button"
                              aria-label={t(I18nKey.INTEGRATIONS_HUB$DENY)}
                              className="inline-flex size-6 items-center justify-center rounded-full text-[var(--oh-danger)] hover:bg-[color:rgba(231,106,94,0.12)]"
                              onClick={(event) => {
                                event.stopPropagation();
                                decideApprovals([approval.id], "denied");
                              }}
                            >
                              <X size={14} aria-hidden />
                            </button>
                          </ActionIconTooltip>
                        </div>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {approveIds ? (
        <ApproveSelectedModal
          selectedCount={approveIds.length}
          onClose={() => setApproveIds(null)}
          onConfirm={(durationHours) => {
            decideApprovals(approveIds, "approved", durationHours);
            setApproveIds(null);
            setSelected({});
          }}
        />
      ) : null}
    </div>
  );
}
