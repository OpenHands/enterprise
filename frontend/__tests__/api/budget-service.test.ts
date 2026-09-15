import { afterEach, expect, it, vi } from "vitest";
import { budgetService } from "#/api/budget-service/budget-service.api";
import { openHands } from "#/api/open-hands-axios";
import type { BudgetAdoptionRequest } from "#/api/budget-service/budget-service.types";

afterEach(() => vi.restoreAllMocks());

it("loads alert preferences from the explicitly selected organization", async () => {
  const get = vi
    .spyOn(openHands, "get")
    .mockResolvedValue({ data: { thresholds: [] } });
  expect(await budgetService.getNotifications("org/other")).toEqual({
    thresholds: [],
  });
  expect(get).toHaveBeenCalledExactlyOnceWith(
    "/api/organizations/org%2Fother/budgets/notifications",
  );
});

it("saves alert preferences through a separate versioned endpoint", async () => {
  const put = vi
    .spyOn(openHands, "put")
    .mockResolvedValue({ data: { fingerprint: "b".repeat(64) } });
  const preferences = {
    expected_fingerprint: "a".repeat(64),
    thresholds: [],
    slack_channel: null,
    slack_team_id: null,
  };
  await budgetService.updateNotifications({
    orgId: "org-a",
    request: preferences,
  });
  expect(put).toHaveBeenCalledExactlyOnceWith(
    "/api/organizations/org-a/budgets/notifications",
    preferences,
  );
});

const request: BudgetAdoptionRequest = {
  preview_fingerprint: "a".repeat(64),
  idempotency_key: "stable-request",
  current_team_allowance: 0,
  current_default_member_allowance: null,
  future_monthly_limit: 100,
  future_default_member_limit: null,
  reset_day: 15,
  replace_native_reset_schedules: true,
};

it("preserves a 202 pending response and explicit zero/unlimited allowances", async () => {
  const pending = { operation_id: "op-1", status: "pending" };
  const post = vi
    .spyOn(openHands, "post")
    .mockResolvedValue({ status: 202, data: pending });
  expect(await budgetService.confirm({ orgId: "org-a", request })).toBe(
    pending,
  );
  expect(post).toHaveBeenCalledExactlyOnceWith(
    "/api/organizations/org-a/budgets/adoption",
    request,
  );
});

it("uses an operation-specific retry without sending replacement allowance values", async () => {
  const post = vi
    .spyOn(openHands, "post")
    .mockResolvedValue({ data: { status: "applied" } });
  await budgetService.retry({ orgId: "org-a", operationId: "op-1" });
  expect(post).toHaveBeenCalledExactlyOnceWith(
    "/api/organizations/org-a/budgets/operations/op-1/retry",
  );
});

it("encodes path identities instead of allowing another endpoint to be selected", async () => {
  const get = vi.spyOn(openHands, "get").mockResolvedValue({ data: {} });
  await budgetService.getOperation({
    orgId: "org/other",
    operationId: "operation?replacement",
  });
  expect(get).toHaveBeenCalledExactlyOnceWith(
    "/api/organizations/org%2Fother/budgets/operations/operation%3Freplacement",
  );
});

it("does not turn a conflict into a successful budget response", async () => {
  const conflict = new Error("Budget policy changed");
  vi.spyOn(openHands, "post").mockRejectedValue(conflict);
  await expect(
    budgetService.handOff({
      orgId: "org-a",
      request: { expected_generation: 2 },
    }),
  ).rejects.toBe(conflict);
});
