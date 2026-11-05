import React from "react";
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  QueryClient,
  QueryClientProvider,
  isCancelledError,
} from "@tanstack/react-query";
import { useLogout } from "#/hooks/mutation/use-logout";
import AuthService from "#/api/auth-service/auth-service.api";
import { clearLoginData } from "#/utils/local-storage";

vi.mock("#/api/auth-service/auth-service.api");
vi.mock("#/utils/local-storage");
vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => ({ data: { app_mode: "saas" } }),
}));
const { reset } = vi.hoisted(() => ({ reset: vi.fn() }));
vi.mock("posthog-js/react", () => ({ usePostHog: () => ({ reset }) }));

describe("useLogout", () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );

  afterEach(() => {
    client.clear();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("cancels in-flight queries before invalidating the session and clears login data on success", async () => {
    const reload = vi.fn();
    const testWindow = Object.create(window);
    Object.defineProperty(testWindow, "location", { value: { reload } });
    vi.stubGlobal("window", testWindow);
    const polling = client
      .fetchQuery({
        queryKey: ["tasks"],
        queryFn: () => new Promise(() => {}),
      })
      .catch((error) => error);
    vi.mocked(AuthService.logout).mockImplementation(async () => {
      expect(client.isFetching()).toBe(0);
    });
    const { result } = renderHook(() => useLogout(), { wrapper });

    await act(() => result.current.mutateAsync());

    expect(isCancelledError(await polling)).toBe(true);
    expect(AuthService.logout).toHaveBeenCalledWith("saas");
    expect(clearLoginData).toHaveBeenCalledOnce();
    expect(reset).toHaveBeenCalledOnce();
    expect(reload).toHaveBeenCalledOnce();
  });

  it("keeps login data and allows retry after a logout failure", async () => {
    vi.mocked(AuthService.logout).mockRejectedValue(
      new Error("Service unavailable"),
    );
    const { result } = renderHook(() => useLogout(), { wrapper });

    await act(async () => {
      await expect(result.current.mutateAsync()).rejects.toThrow(
        "Service unavailable",
      );
    });

    expect(clearLoginData).not.toHaveBeenCalled();
    expect(reset).not.toHaveBeenCalled();
    expect(result.current.isPending).toBe(false);
    expect(client.isMutating()).toBe(0);
  });
});
