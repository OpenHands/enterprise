import { afterEach, expect, it } from "vitest";
import {
  clearBudgetSubmission,
  readBudgetSubmission,
  saveBudgetSubmission,
} from "#/utils/budget-submission";
import { adoptionRequest } from "../helpers/budget-control";

const saved = {
  submission: { kind: "adopt" as const, request: adoptionRequest },
  operationId: null,
};

afterEach(() => sessionStorage.clear());

it("preserves explicit zero, unlimited overrides and the reviewed request", () => {
  saveBudgetSubmission("org-a", saved);
  expect(readBudgetSubmission("org-a")).toEqual(saved);
  expect(readBudgetSubmission("org-b")).toBeNull();
});

it("does not replace unresolved intent even with the same idempotency key", () => {
  saveBudgetSubmission("org-a", saved);
  expect(() =>
    saveBudgetSubmission("org-a", {
      ...saved,
      submission: {
        kind: "adopt",
        request: { ...adoptionRequest, current_team_allowance: 500 },
      },
    }),
  ).toThrow("Resolve the previous budget request");
  expect(readBudgetSubmission("org-a")).toEqual(saved);
});

it("only clears the specific request being acknowledged", () => {
  saveBudgetSubmission("org-a", saved);
  clearBudgetSubmission("org-a", "different-request");
  clearBudgetSubmission("org-b", adoptionRequest.idempotency_key);
  expect(readBudgetSubmission("org-a")).toEqual(saved);
  clearBudgetSubmission("org-a", adoptionRequest.idempotency_key);
  expect(readBudgetSubmission("org-a")).toBeNull();
});

it.each([
  { ...saved, version: 2, orgId: "org-a" },
  { ...saved, version: 1, orgId: "org-b" },
  { ...saved, version: 1, orgId: "org-a", submission: {} },
  { ...saved, version: 1, orgId: "org-a", operationId: 3 },
])("rejects incompatible or cross-organization saved state: %j", (value) => {
  sessionStorage.setItem(
    "openhands:budget-submission:org-a",
    JSON.stringify(value),
  );
  expect(() => readBudgetSubmission("org-a")).toThrow();
  expect(() => saveBudgetSubmission("org-a", saved)).toThrow();
});

it.each([
  { current_team_allowance: -1 },
  { current_team_allowance: Infinity },
  { future_monthly_limit: 0 },
  { future_member_limits: { "member-1": 0 } },
  { reset_day: 2 },
  { preview_fingerprint: "invalid" },
])("does not save invalid policy: %j", (changes) => {
  expect(() =>
    saveBudgetSubmission("org-a", {
      ...saved,
      submission: {
        kind: "adopt",
        request: { ...adoptionRequest, ...changes } as typeof adoptionRequest,
      },
    }),
  ).toThrow("invalid");
  expect(readBudgetSubmission("org-a")).toBeNull();
});

it("round-trips a managed update without dropping off-page overrides", () => {
  const managed = {
    submission: {
      kind: "settings" as const,
      request: {
        idempotency_key: "edit-request",
        expected_generation: 1,
        enabled: true,
        current_cycle_team_allowance: 0,
        current_cycle_default_member_allowance: null,
        current_cycle_member_allowances: { "off-page": 100 },
        future_monthly_limit: 200,
        future_default_member_limit: 10,
        future_member_limits: { "off-page": 150 },
        reset_day: 1 as const,
      },
    },
    operationId: "edit-operation",
  };
  saveBudgetSubmission("org-a", managed);
  expect(readBudgetSubmission("org-a")).toEqual(managed);
});
