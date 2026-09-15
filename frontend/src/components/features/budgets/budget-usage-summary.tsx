/* eslint-disable i18next/no-literal-string */
import type { OrgBudgetSettings } from "#/api/organization-service/organization-service.api";
import { SpendMeter } from "./budgets-components";

const money = (value: number | null) =>
  value === null
    ? "—"
    : `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function BudgetUsageSummary({ budget }: { budget: OrgBudgetSettings }) {
  const managed = budget.control_mode === "managed";
  return (
    <section className="space-y-4 rounded-lg border border-neutral-700 p-5">
      <h2 className="text-lg font-semibold">Current budget usage</h2>
      {budget.spend_status === "unavailable" ? (
        <p>
          Spend data is temporarily unavailable. LiteLLM remains the enforcement
          point, but this page cannot confirm current usage.
        </p>
      ) : (
        <p className="text-sm text-neutral-400">
          {budget.spend_status === "stale"
            ? "Showing the last successful LiteLLM snapshot"
            : "Spend reported by LiteLLM"}
          {budget.spend_observed_at
            ? ` from ${new Date(budget.spend_observed_at).toLocaleString()}.`
            : "."}{" "}
          LiteLLM performs final request admission, so the latest request may
          not appear here yet.
        </p>
      )}
      <p className="text-xs text-neutral-400">
        Includes app, automation, and SDK requests routed through this
        deployment&apos;s LiteLLM proxy. Requests sent directly to an external
        provider are outside this budget.
      </p>
      <div className="text-2xl font-semibold">
        {money(budget.current_spend)}
      </div>
      {managed && (
        <>
          <p>
            Current-cycle allowance: {money(budget.current_cycle_allowance)}.
            Cycle ends:{" "}
            {new Date(budget.cycle_end_at).toLocaleString(undefined, {
              timeZone: "UTC",
            })}{" "}
            UTC.
          </p>
          {budget.current_spend_percentage !== null && (
            <SpendMeter percentage={budget.current_spend_percentage} />
          )}
          <p
            role={
              budget.reconciliation_state === "healthy" ||
              budget.reconciliation_state === "inactive"
                ? "status"
                : "alert"
            }
          >
            {budget.reconciliation_state === "healthy"
              ? "Verified budget policy."
              : `Reconciliation: ${budget.reconciliation_state}.`}
            {budget.reconciliation_error
              ? ` ${budget.reconciliation_error}`
              : ""}
          </p>
        </>
      )}
      {budget.applied_team_max_budget !== null && (
        <p>
          Applied LiteLLM team cap: {money(budget.applied_team_max_budget)}.
        </p>
      )}
      {budget.unmapped_member_count !== null &&
        budget.unmapped_member_count > 0 && (
          <p>
            {`${budget.unmapped_member_count} LiteLLM ${budget.unmapped_member_count === 1 ? "identity is" : "identities are"} not mapped to organization users. `}
            {budget.unmapped_spend === null
              ? "Their cycle-level attribution is unavailable."
              : `${money(budget.unmapped_spend)} of this cycle's spend is attributed to them.`}
          </p>
        )}
      <table className="w-full text-left text-sm">
        <thead>
          <tr>
            <th>Member</th>
            <th>Current-cycle spend</th>
          </tr>
        </thead>
        <tbody>
          {budget.users.map((member) => (
            <tr key={member.user_id}>
              <td className="break-all py-2">
                {member.user_name || member.user_email || member.user_id}
              </td>
              <td>{money(member.current_spend)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
