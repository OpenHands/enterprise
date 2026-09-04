/* eslint-disable i18next/no-literal-string */
import React from "react";
import { Link } from "react-router";
import {
  EmailIcon,
  HashIcon,
  SearchIcon,
  SlackIcon,
} from "#/components/shared/icons/inline-icons";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import EditIcon from "#/icons/u-edit.svg?react";
import DeleteIcon from "#/icons/u-delete.svg?react";
import {
  PillBadge,
  SpendMeter,
  StatusPill,
  Toggle,
  UserProgressBar,
} from "./budgets-components";
import { cn } from "#/utils/utils";
import {
  formControlFieldClassName,
  formControlInlineInputClassName,
  formControlShellClassName,
} from "#/utils/form-control-classes";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
  settingsListIconActionButtonClassName,
  settingsListRowClassName,
  settingsListTableCellClassName,
  settingsListTableHeadClassName,
  settingsListTableHeaderCellClassName,
  settingsListTableRowClassName,
} from "#/utils/settings-list-classes";

export type BudgetThreshold = {
  percentage: number;
  email_enabled: boolean;
  slack_enabled: boolean;
};

export type BudgetUserRow = {
  user_id: string;
  name: string;
  email: string;
  is_override: boolean;
  is_disabled: boolean;
  effective_monthly_limit?: number | null;
  budgetLabel: string;
  budgetNote: string;
  hasLimit: boolean;
  usage: number | null;
  maxUsage: number;
  status: string;
  statusColor: "green" | "yellow" | "red";
};

const BILLING_CYCLE_ITEMS = [
  { key: "1st", label: "1st of each month" },
  { key: "15th", label: "15th of each month" },
];

const STATUS_FILTER_ITEMS = [
  { key: "all", label: "All statuses" },
  { key: "over80", label: "Over 80%" },
  { key: "over90", label: "Over 90%" },
  { key: "overCap", label: "Over cap" },
  { key: "onTrack", label: "On track" },
  { key: "noCap", label: "No cap" },
  { key: "disabled", label: "Disabled" },
];

interface OrganizationBudgetTabProps {
  orgBudgetEnabled: boolean;
  onToggleOrgBudget: (value: boolean) => void;
  currentSpend: number | null;
  monthlyLimitValue: number | null;
  cycleLabel: string;
  percentage: number | null;
  spendStatus: "live" | "stale" | "unavailable";
  spendObservedAt: string | null;
  syncStatus: string | null;
  syncError: string | null;
  unmappedSpend: number | null;
  unmappedMemberCount: number | null;
  monthlyLimit: string;
  onMonthlyLimitChange: (value: string) => void;
  billingCycle: string;
  onBillingCycleChange: (value: string) => void;
  thresholds: BudgetThreshold[];
  onAddThreshold: () => void;
  onDeleteThreshold: (index: number) => void;
  onToggleEmail: (index: number) => void;
  onToggleSlack: (index: number) => void;
  emailIntegrationEnabled: boolean;
  slackIntegrationEnabled: boolean;
  slackChannel: string;
  onSlackChannelChange: (value: string) => void;
  onReset: () => void;
  onSave: () => void;
  isSaving: boolean;
  isMonthlyLimitValid: boolean;
}

export function OrganizationBudgetTab({
  orgBudgetEnabled,
  onToggleOrgBudget,
  currentSpend,
  monthlyLimitValue,
  cycleLabel,
  percentage,
  spendStatus,
  spendObservedAt,
  syncStatus,
  syncError,
  unmappedSpend,
  unmappedMemberCount,
  monthlyLimit,
  onMonthlyLimitChange,
  billingCycle,
  onBillingCycleChange,
  thresholds,
  onAddThreshold,
  onDeleteThreshold,
  onToggleEmail,
  onToggleSlack,
  emailIntegrationEnabled,
  slackIntegrationEnabled,
  slackChannel,
  onSlackChannelChange,
  onReset,
  onSave,
  isSaving,
  isMonthlyLimitValid,
}: OrganizationBudgetTabProps) {
  const observedAtLabel = spendObservedAt
    ? new Date(spendObservedAt).toLocaleString()
    : null;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-medium text-foreground mb-1">
            Organization monthly budget
          </h2>
          <p className="text-sm text-muted">
            Track total spend across your org and get alerted before you hit
            your cap.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span className="text-sm text-muted">Enable budget</span>
          <Toggle
            enabled={orgBudgetEnabled}
            onChange={onToggleOrgBudget}
            label="Enable organization budget"
          />
        </div>
      </div>

      <div className="rounded-lg border border-border-subtle bg-base-secondary p-6">
        <div
          className={`mb-4 rounded-lg border px-4 py-3 text-sm ${
            spendStatus === "live"
              ? "border-[var(--oh-border)] bg-tertiary text-muted"
              : "border-amber-500/30 bg-amber-500/10 text-amber-300"
          }`}
        >
          {spendStatus === "unavailable" ? (
            <span>
              Spend data is temporarily unavailable. LiteLLM remains the
              enforcement point, but this page cannot confirm current usage.
            </span>
          ) : (
            <span>
              {spendStatus === "stale"
                ? "Showing the last successful LiteLLM snapshot"
                : "Spend reported by LiteLLM"}
              {observedAtLabel ? ` from ${observedAtLabel}.` : "."} LiteLLM
              performs final request admission, so the latest request may not
              appear here yet.
            </span>
          )}
        </div>
        {syncStatus === "error" && (
          <div
            role="alert"
            className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300"
          >
            Budget enforcement reconciliation needs attention. Existing caps are
            preserved until a verified synchronization succeeds.
            {syncError ? ` ${syncError}` : ""}
          </div>
        )}
        <p className="mb-4 text-xs text-[var(--oh-muted)]">
          Includes app, automation, and SDK requests routed through this
          deployment&apos;s LiteLLM proxy. Requests sent directly to an external
          provider are outside this budget.
        </p>
        {unmappedMemberCount !== null && unmappedMemberCount > 0 && (
          <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
            {`${unmappedMemberCount} LiteLLM ${
              unmappedMemberCount === 1 ? "identity is" : "identities are"
            } not mapped to organization users. `}
            {unmappedSpend === null
              ? "Their cycle-level attribution will become available after the next verified reset."
              : `$${unmappedSpend.toLocaleString("en-US", {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })} of this cycle's spend is attributed to them.`}
          </div>
        )}
        <div className="mb-3 flex items-baseline justify-between">
          <div>
            <span className="text-3xl font-bold text-foreground">
              {currentSpend === null
                ? "—"
                : `$${currentSpend.toLocaleString("en-US", {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}`}
            </span>
            <span className="ml-2 text-muted">
              {monthlyLimitValue
                ? `of $${monthlyLimitValue.toLocaleString()} spent in ${cycleLabel}`
                : `spent in ${cycleLabel}`}
            </span>
          </div>
          <span className="text-xl font-semibold text-logo">
            {monthlyLimitValue && percentage !== null
              ? `${percentage.toFixed(1)}%`
              : "—"}
          </span>
        </div>
        {percentage === null ? (
          <div className="h-3 rounded-full bg-tertiary" />
        ) : (
          <SpendMeter percentage={percentage} />
        )}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label
            htmlFor="org-monthly-limit"
            className="mb-2 block text-sm text-white"
          >
            Monthly limit
          </label>
          <div className={cn(formControlShellClassName, "w-full")}>
            <span className="ml-3 shrink-0 text-tertiary-alt" aria-hidden>
              $
            </span>
            <input
              id="org-monthly-limit"
              type="number"
              value={monthlyLimit}
              onChange={(event) => onMonthlyLimitChange(event.target.value)}
              className={cn(formControlInlineInputClassName, "text-white")}
            />
          </div>
        </div>
        <SettingsDropdownInput
          testId="org-billing-cycle"
          name="org-billing-cycle"
          label="Billing cycle resets"
          items={BILLING_CYCLE_ITEMS}
          selectedKey={billingCycle}
          isClearable={false}
          onSelectionChange={(key) => {
            if (key != null) onBillingCycleChange(String(key));
          }}
        />
      </div>

      <div>
        <div className="flex items-center justify-between mb-3 gap-4">
          <div>
            <h3 className="text-sm font-medium text-foreground mb-1">
              Alert thresholds
            </h3>
            <p className="text-xs text-[var(--oh-muted)]">
              Add one or more thresholds. Each can email admins, post to Slack,
              or both.
              {!emailIntegrationEnabled && (
                <>
                  {" "}
                  Email alerts require RESEND_API_KEY or SMTP_* env vars set in
                  the deployment environment and a restart.
                </>
              )}
              {!slackIntegrationEnabled && (
                <>
                  {" "}
                  Slack alerts require the Slack app to be configured in the
                  deployment (SLACK_* env vars). After a restart, connect it in{" "}
                  <Link
                    to="/settings/integrations"
                    className="underline underline-offset-2"
                  >
                    Settings → Integrations
                  </Link>
                  .
                </>
              )}
            </p>
          </div>
          <BrandButton
            type="button"
            variant="secondary"
            onClick={onAddThreshold}
            className="shrink-0 whitespace-nowrap"
          >
            + Add threshold
          </BrandButton>
        </div>

        <div
          className={cn(
            settingsListContainerClassName,
            settingsListDividerClassName,
          )}
        >
          {thresholds.map((threshold, index) => {
            const thresholdAmount = monthlyLimitValue
              ? (monthlyLimitValue * threshold.percentage) / 100
              : null;
            return (
              <div
                key={threshold.percentage}
                className={cn(
                  settingsListRowClassName,
                  "h-auto min-h-12 gap-4 py-3",
                )}
              >
                <div className="w-16 shrink-0">
                  <span className="text-foreground font-medium">
                    {threshold.percentage}%
                  </span>
                </div>
                <div className="w-28 shrink-0">
                  <span className="text-muted text-sm">
                    {thresholdAmount !== null
                      ? `Triggers at $${thresholdAmount.toLocaleString()}`
                      : "Set a monthly limit to calculate"}
                  </span>
                </div>
                <div className="flex items-center gap-2 flex-1 min-w-0">
                  <button
                    type="button"
                    onClick={() => onToggleEmail(index)}
                    disabled={!emailIntegrationEnabled}
                    className="flex items-center gap-1.5 disabled:cursor-not-allowed"
                    title={
                      emailIntegrationEnabled
                        ? "Email org admins"
                        : "Email alerts require RESEND_API_KEY or SMTP_* env vars in deployment (restart required)"
                    }
                  >
                    <PillBadge
                      active={
                        emailIntegrationEnabled && threshold.email_enabled
                      }
                      icon={<EmailIcon />}
                      label="Email org admins"
                      disabled={!emailIntegrationEnabled}
                    />
                  </button>
                  <button
                    type="button"
                    onClick={() => onToggleSlack(index)}
                    disabled={!slackIntegrationEnabled}
                    className="flex items-center gap-1.5 disabled:cursor-not-allowed"
                    title={
                      slackIntegrationEnabled
                        ? "Post to Slack"
                        : "Slack integration must be configured in deployment (restart required)"
                    }
                  >
                    <PillBadge
                      active={
                        slackIntegrationEnabled && threshold.slack_enabled
                      }
                      icon={<SlackIcon />}
                      label="# Post to Slack"
                      disabled={!slackIntegrationEnabled}
                    />
                  </button>
                </div>
                <button
                  type="button"
                  onClick={() => onDeleteThreshold(index)}
                  aria-label={`Delete ${threshold.percentage}% threshold`}
                  className={settingsListIconActionButtonClassName}
                >
                  <DeleteIcon width={16} height={16} />
                </button>
              </div>
            );
          })}
        </div>
      </div>

      <div>
        <label
          htmlFor="slack-channel"
          className="mb-2 block text-sm text-white"
        >
          Slack channel
        </label>
        <div className={cn(formControlShellClassName, "w-full")}>
          <span className="ml-3 shrink-0 text-tertiary-alt" aria-hidden>
            <HashIcon />
          </span>
          <input
            id="slack-channel"
            type="text"
            value={slackChannel}
            onChange={(event) => {
              if (!slackIntegrationEnabled) return;
              onSlackChannelChange(event.target.value);
            }}
            disabled={!slackIntegrationEnabled}
            placeholder={
              slackIntegrationEnabled
                ? "#budget-alerts"
                : "Connect Slack to set a channel"
            }
            className={cn(formControlInlineInputClassName, "text-white")}
          />
        </div>
        {slackIntegrationEnabled ? (
          <p className="text-xs text-[var(--oh-muted)] mt-2">
            Used by any threshold with &apos;Post to Slack&apos; enabled.
          </p>
        ) : (
          <p className="text-xs text-muted mt-2">
            Slack alerts are disabled. Please integrate Slack to select a
            channel.
          </p>
        )}
      </div>

      <div className="flex justify-start gap-3">
        <BrandButton
          type="button"
          variant="primary"
          onClick={onSave}
          isDisabled={isSaving || !isMonthlyLimitValid}
        >
          Save changes
        </BrandButton>
        <BrandButton type="button" variant="secondary" onClick={onReset}>
          Reset
        </BrandButton>
      </div>
    </div>
  );
}

interface DefaultBudgetsTabProps {
  defaultAmount: string;
  defaultAmountLabel: string;
  onDefaultAmountChange: (value: string) => void;
  onSave: () => void;
  isSaving: boolean;
}

export function DefaultBudgetsTab({
  defaultAmount,
  defaultAmountLabel,
  onDefaultAmountChange,
  onSave,
  isSaving,
}: DefaultBudgetsTabProps) {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-medium text-foreground mb-1">
          Default budget for new users
        </h2>
        <p className="text-sm text-muted">
          Applied automatically when a user joins your organization. Existing
          users keep their current budgets.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-6">
        <div>
          <div className="block text-sm text-white mb-2">Budget cadence</div>
          <div className={cn(formControlFieldClassName, "flex items-center")}>
            Monthly
          </div>
        </div>
        <div>
          <label
            htmlFor="default-budget-amount"
            className="mb-2 block text-sm text-white"
          >
            Default amount
          </label>
          <div className={cn(formControlShellClassName, "w-full")}>
            <span className="ml-3 shrink-0 text-tertiary-alt" aria-hidden>
              $
            </span>
            <input
              id="default-budget-amount"
              type="number"
              value={defaultAmount}
              onChange={(event) => onDefaultAmountChange(event.target.value)}
              className={cn(formControlInlineInputClassName, "text-white")}
            />
          </div>
        </div>
      </div>

      <div>
        <div className="block text-sm text-white mb-2">Preview</div>
        <p className="text-sm text-muted">
          {`New users get up to $${defaultAmountLabel} per month before requiring an increase.`}
        </p>
      </div>

      <div className="flex justify-start">
        <BrandButton
          type="button"
          variant="primary"
          onClick={onSave}
          isDisabled={isSaving}
        >
          Save default
        </BrandButton>
      </div>
    </div>
  );
}

interface UserOverridesTabProps {
  searchQuery: string;
  statusFilter: string;
  onSearchChange: (value: string) => void;
  onStatusFilterChange: (value: string) => void;
  userRows: BudgetUserRow[];
  editingUserId: string | null;
  overrideAmount: string;
  overrideDisabled: boolean;
  onOverrideAmountChange: (value: string) => void;
  onOverrideDisabledChange: (value: boolean) => void;
  onStartEditing: (user: BudgetUserRow) => void;
  onCancelEditing: () => void;
  onSaveOverride: (userId: string) => void;
  onRemoveOverride: (userId: string) => void;
  isSavingOverride: boolean;
  isDeletingOverride: boolean;
  usersTotal: number;
  usersStart: number;
  usersEnd: number;
  usersPage: number;
  totalPages: number;
  isLoading: boolean;
  onPageChange: (page: number) => void;
}

function UserBudgetUsage({ user }: { user: BudgetUserRow }) {
  if (user.usage === null) {
    return <div className="text-sm text-muted">Unavailable</div>;
  }

  if (user.hasLimit) {
    return (
      <div>
        <UserProgressBar
          value={user.usage}
          max={user.maxUsage}
          status={user.statusColor}
        />
        <div className="mt-1 text-xs text-[var(--oh-muted)]">
          {`$${user.usage.toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })} of $${user.maxUsage.toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })}`}
        </div>
      </div>
    );
  }

  return (
    <div className="text-sm text-muted">
      {`$${user.usage.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })} spent`}
    </div>
  );
}

export function UserOverridesTab({
  searchQuery,
  statusFilter,
  onSearchChange,
  onStatusFilterChange,
  userRows,
  editingUserId,
  overrideAmount,
  overrideDisabled,
  onOverrideAmountChange,
  onOverrideDisabledChange,
  onStartEditing,
  onCancelEditing,
  onSaveOverride,
  onRemoveOverride,
  isSavingOverride,
  isDeletingOverride,
  usersTotal,
  usersStart,
  usersEnd,
  usersPage,
  totalPages,
  isLoading,
  onPageChange,
}: UserOverridesTabProps) {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-medium text-foreground mb-1">
          User budget overrides
        </h2>
        <p className="text-sm text-muted">
          Override the default for individual users — increase, decrease, or
          disable.
        </p>
      </div>

      <div className="flex gap-4 items-end">
        <div className={cn(formControlShellClassName, "flex-1")}>
          <span className="ml-3 shrink-0 text-tertiary-alt" aria-hidden>
            <SearchIcon />
          </span>
          <input
            type="text"
            placeholder="Search users by name or email..."
            value={searchQuery}
            onChange={(event) => onSearchChange(event.target.value)}
            className={cn(formControlInlineInputClassName, "text-white")}
          />
        </div>
        <div className="w-52 shrink-0">
          <SettingsDropdownInput
            testId="budget-status-filter"
            name="budget-status-filter"
            items={STATUS_FILTER_ITEMS}
            selectedKey={statusFilter}
            isClearable={false}
            onSelectionChange={(key) => {
              onStatusFilterChange(key != null ? String(key) : "all");
            }}
          />
        </div>
      </div>

      <div
        className={cn(
          settingsListContainerClassName,
          "min-w-0 overflow-x-auto",
        )}
      >
        <table className="w-full min-w-max">
          <thead className={settingsListTableHeadClassName}>
            <tr>
              <th className={settingsListTableHeaderCellClassName}>User</th>
              <th className={settingsListTableHeaderCellClassName}>Budget</th>
              <th className={settingsListTableHeaderCellClassName}>Usage</th>
              <th className={settingsListTableHeaderCellClassName}>Status</th>
              <th
                className={cn(
                  settingsListTableHeaderCellClassName,
                  "text-right",
                )}
              >
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {userRows.map((user) => {
              const isEditing = editingUserId === user.user_id;
              const overrideValue = Number(overrideAmount);
              const canSaveOverride =
                overrideDisabled ||
                (!!overrideAmount &&
                  !Number.isNaN(overrideValue) &&
                  overrideValue > 0);

              return (
                <tr
                  key={user.user_id}
                  className={settingsListTableRowClassName}
                >
                  <td
                    className={cn(
                      settingsListTableCellClassName,
                      "h-auto py-3",
                    )}
                  >
                    <div>
                      <div className="text-foreground font-medium">
                        {user.name}
                      </div>
                      <div className="text-sm text-[var(--oh-muted)]">
                        {user.email || "-"}
                      </div>
                    </div>
                  </td>
                  <td
                    className={cn(
                      settingsListTableCellClassName,
                      "h-auto py-3",
                    )}
                  >
                    {isEditing ? (
                      <div className="space-y-2">
                        <div className="flex items-center gap-2">
                          <div
                            className={cn(formControlShellClassName, "w-28")}
                          >
                            <span
                              className="ml-3 shrink-0 text-tertiary-alt"
                              aria-hidden
                            >
                              $
                            </span>
                            <input
                              type="number"
                              min="0"
                              step="0.01"
                              value={overrideAmount}
                              onChange={(event) =>
                                onOverrideAmountChange(event.target.value)
                              }
                              disabled={overrideDisabled}
                              className={cn(
                                formControlInlineInputClassName,
                                "text-white",
                              )}
                            />
                          </div>
                          <span className="text-xs text-[var(--oh-muted)]">
                            / month
                          </span>
                        </div>
                        <label className="flex items-center gap-2 text-xs text-muted">
                          <input
                            type="checkbox"
                            checked={overrideDisabled}
                            onChange={(event) =>
                              onOverrideDisabledChange(event.target.checked)
                            }
                            className="accent-primary"
                          />
                          Disable budget for this user
                        </label>
                      </div>
                    ) : (
                      <>
                        <div className="text-foreground">
                          {user.budgetLabel}
                        </div>
                        <div className="flex items-center gap-1.5 text-xs text-[var(--oh-muted)]">
                          {user.is_override && (
                            <span className="w-1.5 h-1.5 rounded-full bg-primary" />
                          )}
                          {user.budgetNote}
                        </div>
                      </>
                    )}
                  </td>
                  <td
                    className={cn(
                      settingsListTableCellClassName,
                      "h-auto py-3 min-w-[180px]",
                    )}
                  >
                    <UserBudgetUsage user={user} />
                  </td>
                  <td
                    className={cn(
                      settingsListTableCellClassName,
                      "h-auto py-3",
                    )}
                  >
                    <StatusPill status={user.status} />
                  </td>
                  <td
                    className={cn(
                      settingsListTableCellClassName,
                      "h-auto py-3 text-right",
                    )}
                  >
                    {isEditing ? (
                      <div className="flex items-center justify-end gap-2">
                        <BrandButton
                          type="button"
                          variant="primary"
                          onClick={() => onSaveOverride(user.user_id)}
                          isDisabled={!canSaveOverride || isSavingOverride}
                        >
                          Save
                        </BrandButton>
                        <BrandButton
                          type="button"
                          variant="secondary"
                          onClick={onCancelEditing}
                        >
                          Cancel
                        </BrandButton>
                      </div>
                    ) : (
                      <div className="ml-auto flex w-fit items-center justify-end gap-0.5">
                        <button
                          type="button"
                          onClick={() => onStartEditing(user)}
                          aria-label={`Edit budget for ${user.name}`}
                          className={settingsListIconActionButtonClassName}
                        >
                          <EditIcon width={16} height={16} />
                        </button>
                        {user.is_override && (
                          <button
                            type="button"
                            onClick={() => onRemoveOverride(user.user_id)}
                            disabled={isDeletingOverride}
                            aria-label={`Remove override for ${user.name}`}
                            className={cn(
                              settingsListIconActionButtonClassName,
                              "disabled:opacity-60",
                            )}
                          >
                            <DeleteIcon width={16} height={16} />
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {usersTotal > 0 && (
        <div className="flex flex-col gap-3 text-sm text-[var(--oh-muted)] sm:flex-row sm:items-center sm:justify-between">
          <span>{`Showing ${usersStart}-${usersEnd} of ${usersTotal}`}</span>
          <div className="flex items-center gap-2">
            <BrandButton
              type="button"
              variant="secondary"
              onClick={() => onPageChange(Math.max(1, usersPage - 1))}
              isDisabled={usersPage <= 1 || isLoading}
            >
              Previous
            </BrandButton>
            <span className="text-xs text-[var(--oh-muted)]">
              {`${usersPage} / ${totalPages}`}
            </span>
            <BrandButton
              type="button"
              variant="secondary"
              onClick={() => onPageChange(Math.min(totalPages, usersPage + 1))}
              isDisabled={usersPage >= totalPages || isLoading}
            >
              Next
            </BrandButton>
          </div>
        </div>
      )}

      {userRows.length === 0 && (
        <div className="py-12 text-center text-[var(--oh-muted)]">
          No users found matching your criteria.
        </div>
      )}
    </div>
  );
}
