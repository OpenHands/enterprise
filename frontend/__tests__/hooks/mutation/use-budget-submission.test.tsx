import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { budgetService } from "#/api/budget-service/budget-service.api";
import { useBudgetSubmission } from "#/hooks/mutation/use-budget-submission";
import { readBudgetSubmission } from "#/utils/budget-submission";
import { budgetOperationKey } from "#/hooks/query/use-budget-control";
import {
  adoptionRequest,
  pendingOperation,
} from "../../helpers/budget-control";

const submission = { kind: "adopt" as const, request: adoptionRequest };
const variables = { orgId: "org-a", submission };
function setup() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: 3 } },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

it("records original intent before the first financial request", async () => {
  const confirm = vi
    .spyOn(budgetService, "confirm")
    .mockImplementation(async () => {
      expect(readBudgetSubmission("org-a")).toEqual({
        submission,
        operationId: null,
      });
      return pendingOperation;
    });
  const { client, wrapper } = setup();
  const { result } = renderHook(useBudgetSubmission, { wrapper });
  act(() => result.current.mutate(variables));
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(confirm).toHaveBeenCalledOnce();
  expect(readBudgetSubmission("org-a")).toEqual({
    submission,
    operationId: "operation-1",
  });
  expect(result.current.data?.status).toBe("pending");
  client.clear();
});

it("replays the exact request after a lost response and browser remount", async () => {
  const confirm = vi
    .spyOn(budgetService, "confirm")
    .mockRejectedValueOnce(new Error("Response lost"))
    .mockResolvedValue({
      ...pendingOperation,
      status: "applied",
      control_mode: "managed",
    });
  const first = setup();
  const mounted = renderHook(useBudgetSubmission, { wrapper: first.wrapper });
  act(() => mounted.result.current.mutate(variables));
  await waitFor(() => expect(mounted.result.current.isError).toBe(true));
  expect(confirm).toHaveBeenCalledOnce();
  mounted.unmount();
  first.client.clear();

  const second = setup();
  const remounted = renderHook(useBudgetSubmission, {
    wrapper: second.wrapper,
  });
  const recovered = readBudgetSubmission("org-a")!;
  act(() =>
    remounted.result.current.mutate({
      orgId: "org-a",
      submission: recovered.submission,
    }),
  );
  await waitFor(() => expect(remounted.result.current.isSuccess).toBe(true));
  expect(confirm.mock.calls[0]).toEqual(confirm.mock.calls[1]);
  expect(
    second.client.getQueryData(budgetOperationKey("org-a", "operation-1")),
  ).toMatchObject({ status: "applied" });
  expect(readBudgetSubmission("org-a")?.operationId).toBe("operation-1");
  second.client.clear();
});

it("does not write when the browser cannot save recovery intent", async () => {
  const confirm = vi.spyOn(budgetService, "confirm");
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("Storage unavailable");
  });
  const { client, wrapper } = setup();
  const { result } = renderHook(useBudgetSubmission, { wrapper });
  act(() => result.current.mutate(variables));
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(confirm).not.toHaveBeenCalled();
  client.clear();
});

it("does not overwrite unresolved policy with a new submission", async () => {
  const confirm = vi
    .spyOn(budgetService, "confirm")
    .mockRejectedValue(new Error("Response lost"));
  const { client, wrapper } = setup();
  const { result } = renderHook(useBudgetSubmission, { wrapper });
  act(() => result.current.mutate(variables));
  await waitFor(() => expect(result.current.isError).toBe(true));
  await act(async () => {
    await result.current
      .mutateAsync({
        orgId: "org-a",
        submission: {
          kind: "adopt",
          request: { ...adoptionRequest, idempotency_key: "new" },
        },
      })
      .catch(() => {});
  });
  expect(confirm).toHaveBeenCalledOnce();
  expect(readBudgetSubmission("org-a")?.submission).toEqual(submission);
  client.clear();
});
