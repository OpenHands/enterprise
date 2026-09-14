import type { OrgBudgetSettings } from "#/api/organization-service/organization-service.api";
import type { BudgetPreview } from "#/api/budget-service/budget-service.types";
import type { BudgetSubmission } from "#/utils/budget-submission";

// Undefined inherits a member default; null explicitly removes a member limit.
export type AllowanceDraft = string | null | undefined;
export interface BudgetPolicyDraft {
  enabled: boolean;
  currentTeam: string;
  futureTeam: string;
  currentDefault: AllowanceDraft;
  futureDefault: AllowanceDraft;
  currentMembers: Record<string, AllowanceDraft>;
  futureMembers: Record<string, AllowanceDraft>;
  resetDay: 1 | 15;
  replaceResets: boolean;
}

const draftMap = (values: Record<string, number | null>) =>
  Object.fromEntries(
    Object.entries(values).map(([id, value]) => [
      id,
      value?.toString() ?? null,
    ]),
  );

export function initialBudgetDraft(
  budget?: OrgBudgetSettings,
): BudgetPolicyDraft {
  return {
    enabled: budget?.enabled ?? true,
    currentTeam: budget?.current_cycle_allowance?.toString() ?? "",
    futureTeam: budget?.monthly_limit?.toString() ?? "",
    currentDefault: budget
      ? (budget.current_cycle_default_member_allowance?.toString() ?? null)
      : "",
    futureDefault: budget
      ? (budget.default_user_monthly_limit?.toString() ?? null)
      : "",
    currentMembers: draftMap(budget?.current_cycle_member_allowances ?? {}),
    futureMembers: draftMap(budget?.future_member_limits ?? {}),
    resetDay: budget?.reset_day === 15 ? 15 : 1,
    replaceResets: false,
  };
}

function parseAmount(value: string, positive = false): number {
  if (!/^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(value.trim()))
    throw new Error("Invalid allowance");
  const amount = Number(value);
  if (!Number.isFinite(amount) || (positive ? amount <= 0 : amount < 0))
    throw new Error("Invalid allowance");
  return amount;
}

const parseDefault = (value: AllowanceDraft, positive = false) =>
  value === null ? null : parseAmount(value ?? "", positive);

function parseMembers(
  values: Record<string, AllowanceDraft>,
  positive = false,
) {
  return Object.fromEntries(
    Object.entries(values)
      .filter(([, value]) => value !== undefined)
      .map(([id, value]) => [id, parseDefault(value, positive)]),
  );
}

export function buildBudgetSubmission(
  draft: BudgetPolicyDraft,
  idempotencyKey: string,
  source: { preview: BudgetPreview } | { budget: OrgBudgetSettings },
): BudgetSubmission {
  const common = {
    idempotency_key: idempotencyKey,
    future_monthly_limit: parseAmount(draft.futureTeam, true),
    future_default_member_limit: parseDefault(draft.futureDefault, true),
    future_member_limits: parseMembers(draft.futureMembers, true),
    reset_day: draft.resetDay,
  };
  if ("preview" in source) {
    return {
      kind: "adopt",
      request: {
        ...common,
        preview_fingerprint: source.preview.fingerprint,
        current_team_allowance: parseAmount(draft.currentTeam),
        current_default_member_allowance: parseDefault(draft.currentDefault),
        current_member_allowances: parseMembers(draft.currentMembers),
        replace_native_reset_schedules: draft.replaceResets,
      },
    };
  }
  return {
    kind: "settings",
    request: {
      ...common,
      expected_generation: source.budget.control_generation,
      enabled: draft.enabled,
      current_cycle_team_allowance: parseAmount(draft.currentTeam),
      current_cycle_default_member_allowance: parseDefault(
        draft.currentDefault,
      ),
      current_cycle_member_allowances: parseMembers(draft.currentMembers),
    },
  };
}
