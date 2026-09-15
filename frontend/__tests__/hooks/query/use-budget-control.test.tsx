import { act, renderHook, waitFor } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { budgetService } from "#/api/budget-service/budget-service.api";
import {
  useBudgetAdoptionPreview,
  useBudgetOperation,
} from "#/hooks/query/use-budget-control";

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: 3 } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

afterEach(() => {
  vi.restoreAllMocks();
  focusManager.setFocused(undefined);
});

it("does not fetch a preview before the admin requests it", async () => {
  const preview = vi
    .spyOn(budgetService, "preview")
    .mockRejectedValue(new Error("Preview unavailable"));
  const { client, wrapper } = setup();
  const { result, rerender } = renderHook(
    ({ enabled }) => useBudgetAdoptionPreview("org-a", enabled),
    {
      initialProps: { enabled: false },
      wrapper,
    },
  );
  expect(preview).not.toHaveBeenCalled();
  rerender({ enabled: true });
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(preview).toHaveBeenCalledExactlyOnceWith("org-a");
  client.clear();
});

it("does not silently replace a reviewed preview on window focus", async () => {
  const preview = vi
    .spyOn(budgetService, "preview")
    .mockRejectedValue(new Error("Preview unavailable"));
  const { client, wrapper } = setup();
  client.setQueryData(["budget-control", "org-a", "preview"], {
    fingerprint: "reviewed",
  });
  const { result } = renderHook(() => useBudgetAdoptionPreview("org-a", true), {
    wrapper,
  });
  expect(result.current.data?.fingerprint).toBe("reviewed");
  await act(async () => {
    focusManager.setFocused(false);
    focusManager.setFocused(true);
  });
  expect(preview).not.toHaveBeenCalled();
  expect(result.current.data?.fingerprint).toBe("reviewed");
  client.clear();
});

it("does not fetch operation status without an operation identity", () => {
  const read = vi.spyOn(budgetService, "getOperation");
  const { client, wrapper } = setup();
  renderHook(() => useBudgetOperation("org-a", null), { wrapper });
  expect(read).not.toHaveBeenCalled();
  client.clear();
});

it("keeps an unavailable status as an error rather than declaring completion", async () => {
  const read = vi
    .spyOn(budgetService, "getOperation")
    .mockRejectedValue(new Error("Status unavailable"));
  const { client, wrapper } = setup();
  const { result, unmount } = renderHook(
    () => useBudgetOperation("org-a", "op-1"),
    { wrapper },
  );
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.data).toBeUndefined();
  expect(read).toHaveBeenCalledExactlyOnceWith({
    orgId: "org-a",
    operationId: "op-1",
  });
  unmount();
  client.clear();
});
