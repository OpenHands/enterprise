import type {
  BudgetAdoptionRequest,
  BudgetPolicyUpdate,
} from "#/api/budget-service/budget-service.types";

export type BudgetSubmission =
  | { kind: "adopt"; request: BudgetAdoptionRequest }
  | { kind: "settings"; request: BudgetPolicyUpdate };

export interface SavedBudgetSubmission {
  submission: BudgetSubmission;
  operationId: string | null;
}

const storageKey = (orgId: string) => `openhands:budget-submission:${orgId}`;

function amount(value: unknown, positive = false): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    (positive ? value > 0 : value >= 0)
  );
}

const optionalAmount = (value: unknown, positive = false) =>
  value === null || amount(value, positive);

function amountMap(value: unknown, positive = false): boolean {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.values(value).every((item) => optionalAmount(item, positive))
  );
}

function validSubmission(value: unknown): value is BudgetSubmission {
  if (!value || typeof value !== "object") return false;
  const { kind, request } = value as Record<string, unknown>;
  if (!request || typeof request !== "object") return false;
  const r = request as Record<string, unknown>;
  if (
    typeof r.idempotency_key !== "string" ||
    !r.idempotency_key ||
    !amount(r.future_monthly_limit, true) ||
    !optionalAmount(r.future_default_member_limit, true) ||
    !amountMap(r.future_member_limits ?? {}, true) ||
    (r.reset_day !== 1 && r.reset_day !== 15)
  )
    return false;
  if (kind === "adopt") {
    return (
      typeof r.preview_fingerprint === "string" &&
      /^[0-9a-f]{64}$/.test(r.preview_fingerprint) &&
      typeof r.replace_native_reset_schedules === "boolean" &&
      amount(r.current_team_allowance) &&
      optionalAmount(r.current_default_member_allowance) &&
      amountMap(r.current_member_allowances ?? {})
    );
  }
  return (
    kind === "settings" &&
    typeof r.enabled === "boolean" &&
    Number.isSafeInteger(r.expected_generation) &&
    (r.expected_generation as number) >= 0 &&
    amount(r.current_cycle_team_allowance) &&
    optionalAmount(r.current_cycle_default_member_allowance) &&
    amountMap(r.current_cycle_member_allowances)
  );
}

export function readBudgetSubmission(
  orgId: string,
): SavedBudgetSubmission | null {
  const encoded = sessionStorage.getItem(storageKey(orgId));
  if (!encoded) return null;
  const saved = JSON.parse(encoded);
  if (
    saved?.version !== 1 ||
    saved.orgId !== orgId ||
    !validSubmission(saved.submission) ||
    !(saved.operationId === null || typeof saved.operationId === "string")
  )
    throw new Error("The saved budget request could not be read");
  return { submission: saved.submission, operationId: saved.operationId };
}

export function saveBudgetSubmission(
  orgId: string,
  saved: SavedBudgetSubmission,
) {
  if (!validSubmission(saved.submission))
    throw new Error("The budget request is invalid");
  const previous = readBudgetSubmission(orgId);
  if (
    previous &&
    JSON.stringify(previous.submission) !== JSON.stringify(saved.submission)
  )
    throw new Error(
      "Resolve the previous budget request before making changes",
    );
  sessionStorage.setItem(
    storageKey(orgId),
    JSON.stringify({ version: 1, orgId, ...saved }),
  );
}

export function clearBudgetSubmission(orgId: string, idempotencyKey: string) {
  const saved = readBudgetSubmission(orgId);
  if (saved?.submission.request.idempotency_key === idempotencyKey)
    sessionStorage.removeItem(storageKey(orgId));
}

export function clearBudgetSubmissionAfterHandoff(orgId: string) {
  // A verified handoff fences all older intent, including unreadable saved data.
  sessionStorage.removeItem(storageKey(orgId));
}
