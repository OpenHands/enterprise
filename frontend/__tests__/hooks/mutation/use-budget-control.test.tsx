import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { budgetService } from "#/api/budget-service/budget-service.api";
import type {
  BudgetAdoptionRequest,
  BudgetOperation,
} from "#/api/budget-service/budget-service.types";
import {
  useConfirmBudgetAdoption,
  useHandOffBudgetControl,
  useRetryBudgetOperation,
} from "#/hooks/mutation/use-budget-control";
import { budgetOperationKey } from "#/hooks/query/use-budget-control";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";

const request: BudgetAdoptionRequest = {
  preview_fingerprint: "a".repeat(64),
  idempotency_key: "reviewed-request",
  current_team_allowance: 100,
  current_default_member_allowance: 10,
  future_monthly_limit: 700,
  future_default_member_limit: 80,
  reset_day: 1,
  replace_native_reset_schedules: true,
};

const pending: BudgetOperation = {
  operation_id: "operation-1",
  operation_generation: 1,
  kind: "adopt",
  status: "pending",
  control_mode: "needs_adoption",
  generation: 1,
  error: "Not yet verified",
  created_at: "2026-09-14T12:00:00Z",
  finished_at: null,
  current_allowances: { team: 100, default_member: 10, members: {} },
  future_policy: {
    monthly_limit: 700,
    default_user_monthly_limit: 80,
    member_limits: {},
    reset_day: 1,
  },
  cycle_end_at: "2026-10-01T00:00:00Z",
  team_block: { initial: false, target: false, budget_owned: false },
};

function setup() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: 3 }, queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

afterEach(() => {
  vi.restoreAllMocks();
  useSelectedOrganizationStore.setState({ organizationId: null });
});

describe("budget control mutations", () => {
  it("retains pending status and the reviewed idempotency key", async () => {
    const confirm = vi
      .spyOn(budgetService, "confirm")
      .mockResolvedValue(pending);
    const { client, wrapper } = setup();
    const { result } = renderHook(useConfirmBudgetAdoption, { wrapper });
    act(() => result.current.mutate({ orgId: "org-a", request }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.status).toBe("pending");
    expect(result.current.data?.control_mode).toBe("needs_adoption");
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm.mock.calls[0][0]).toEqual({
      orgId: "org-a",
      request,
    });
    expect(
      client.getQueryData(budgetOperationKey("org-a", "operation-1")),
    ).toEqual(pending);
    client.clear();
  });

  it("does not move an in-flight result to a newly selected organization", async () => {
    let finish!: (result: BudgetOperation) => void;
    vi.spyOn(budgetService, "confirm").mockImplementation(
      () =>
        new Promise<BudgetOperation>((resolve) => {
          finish = resolve;
        }),
    );
    const { client, wrapper } = setup();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(useConfirmBudgetAdoption, { wrapper });
    useSelectedOrganizationStore.setState({ organizationId: "org-a" });
    act(() => result.current.mutate({ orgId: "org-a", request }));
    await waitFor(() => expect(finish).toBeDefined());
    useSelectedOrganizationStore.setState({ organizationId: "org-b" });
    act(() => finish(pending));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(
      client.getQueryData(budgetOperationKey("org-a", "operation-1")),
    ).toEqual(pending);
    expect(
      client.getQueryData(budgetOperationKey("org-b", "operation-1")),
    ).toBeUndefined();
    expect(invalidate).toHaveBeenCalledExactlyOnceWith({
      queryKey: ["organizations", "budgets", "org-a"],
    });
    client.clear();
  });

  it("does not automatically repeat an uncertain write", async () => {
    const confirm = vi
      .spyOn(budgetService, "confirm")
      .mockRejectedValue(new Error("Response lost"));
    const { client, wrapper } = setup();
    const { result } = renderHook(useConfirmBudgetAdoption, { wrapper });
    act(() => result.current.mutate({ orgId: "org-a", request }));
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(result.current.variables?.request).toEqual(request);
    expect(
      client.getQueryData(budgetOperationKey("org-a", "operation-1")),
    ).toBeUndefined();
    client.clear();
  });

  it("retries the specific pending operation without constructing new policy", async () => {
    const retry = vi
      .spyOn(budgetService, "retry")
      .mockResolvedValue({
        ...pending,
        status: "applied",
        control_mode: "managed",
        error: null,
      });
    const { client, wrapper } = setup();
    const { result } = renderHook(useRetryBudgetOperation, { wrapper });
    act(() =>
      result.current.mutate({ orgId: "org-a", operationId: "operation-1" }),
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(retry.mock.calls[0][0]).toEqual({
      orgId: "org-a",
      operationId: "operation-1",
    });
    expect(result.current.data?.operation_id).toBe("operation-1");
    expect(result.current.data?.status).toBe("applied");
    client.clear();
  });

  it.each([false, true])(
    "refreshes ownership and operations after handoff (conflict=%s)",
    async (conflict) => {
      const handOff = vi.spyOn(budgetService, "handOff");
      if (conflict) handOff.mockRejectedValue(new Error("Stale generation"));
      else
        handOff.mockResolvedValue({ control_mode: "external", generation: 2 });
      const { client, wrapper } = setup();
      const invalidate = vi.spyOn(client, "invalidateQueries");
      const { result } = renderHook(useHandOffBudgetControl, { wrapper });
      const variables = { orgId: "org-a", request: { expected_generation: 1 } };
      act(() => result.current.mutate(variables));
      await waitFor(() =>
        expect(
          conflict ? result.current.isError : result.current.isSuccess,
        ).toBe(true),
      );
      expect(handOff).toHaveBeenCalledTimes(1);
      expect(handOff.mock.calls[0][0]).toEqual(variables);
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["organizations", "budgets", "org-a"],
      });
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["budget-control", "org-a", "operation"],
      });
      client.clear();
    },
  );
});
