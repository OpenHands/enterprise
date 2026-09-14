import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { BudgetOperationStatus } from "#/components/features/budgets/budget-operation-status";
import { budgetService } from "#/api/budget-service/budget-service.api";
import { budgetOperationKey } from "#/hooks/query/use-budget-control";
import { pendingOperation } from "../../../helpers/budget-control";

const clients: QueryClient[] = [];
function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  const view = render(
    <QueryClientProvider client={client}>
      <BudgetOperationStatus orgId="org-a" operationId="operation-1" />
    </QueryClientProvider>,
  );
  return { client, ...view };
}

afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("never presents a successful HTTP response with pending status as applied", async () => {
  vi.spyOn(budgetService, "getOperation").mockResolvedValue(pendingOperation);
  setup();
  expect(await screen.findByText("BUDGET_CONTROL$PENDING")).toBeVisible();
  expect(screen.queryByText("BUDGET_CONTROL$APPLIED")).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "CONVERSATION$RETRY" }),
  ).toBeEnabled();
});

it("retries only the current operation and refreshes budgets after verification", async () => {
  const user = userEvent.setup();
  vi.spyOn(budgetService, "getOperation").mockResolvedValue(pendingOperation);
  const retry = vi.spyOn(budgetService, "retry").mockResolvedValue({
    ...pendingOperation,
    status: "applied",
    control_mode: "managed",
    error: null,
  });
  const { client } = setup();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  await user.click(
    await screen.findByRole("button", { name: "CONVERSATION$RETRY" }),
  );
  expect(await screen.findByText("BUDGET_CONTROL$APPLIED")).toBeVisible();
  expect(retry.mock.calls[0][0]).toEqual({
    orgId: "org-a",
    operationId: "operation-1",
  });
  expect(
    screen.queryByRole("button", { name: "CONVERSATION$RETRY" }),
  ).not.toBeInTheDocument();
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: ["organizations", "budgets", "org-a"],
  });
});

it("shows unavailable status without assuming either success or no changes", async () => {
  vi.spyOn(budgetService, "getOperation").mockRejectedValue(
    new Error("Unavailable"),
  );
  setup();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "BUDGET_CONTROL$STATUS_UNAVAILABLE",
  );
  expect(screen.queryByText("BUDGET_CONTROL$APPLIED")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "CONVERSATION$RETRY" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "BUTTON$REFRESH" })).toBeEnabled();
});

it("polls a pending operation to completion and then stops polling", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  const read = vi
    .spyOn(budgetService, "getOperation")
    .mockResolvedValue(pendingOperation);
  const { client } = setup();
  await screen.findByText("BUDGET_CONTROL$PENDING");
  const invalidate = vi.spyOn(client, "invalidateQueries");
  read.mockResolvedValue({
    ...pendingOperation,
    status: "applied",
    control_mode: "managed",
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1500);
  });
  expect(await screen.findByText("BUDGET_CONTROL$APPLIED")).toBeVisible();
  expect(read).toHaveBeenCalledTimes(2);
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: ["organizations", "budgets", "org-a"],
  });
  const calls = read.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4500);
  });
  expect(read).toHaveBeenCalledTimes(calls);
});

it("does not retain a retry error after later readback verifies completion", async () => {
  const user = userEvent.setup();
  vi.spyOn(budgetService, "getOperation").mockResolvedValue(pendingOperation);
  vi.spyOn(budgetService, "retry").mockRejectedValue(
    new Error("Response lost"),
  );
  const { client } = setup();
  await user.click(
    await screen.findByRole("button", { name: "CONVERSATION$RETRY" }),
  );
  await screen.findByText("BUDGET_CONTROL$RETRY_FAILED");
  act(() =>
    client.setQueryData(budgetOperationKey("org-a", "operation-1"), {
      ...pendingOperation,
      status: "applied",
      control_mode: "managed",
      error: null,
    }),
  );
  await waitFor(() =>
    expect(screen.getByText("BUDGET_CONTROL$APPLIED")).toBeVisible(),
  );
  expect(
    screen.queryByText("BUDGET_CONTROL$RETRY_FAILED"),
  ).not.toBeInTheDocument();
});

it("does not offer retry for an abandoned operation", async () => {
  vi.spyOn(budgetService, "getOperation").mockResolvedValue({
    ...pendingOperation,
    status: "abandoned",
  });
  setup();
  await screen.findByText("BUDGET_CONTROL$ABANDONED");
  expect(
    screen.queryByRole("button", { name: "CONVERSATION$RETRY" }),
  ).not.toBeInTheDocument();
});
