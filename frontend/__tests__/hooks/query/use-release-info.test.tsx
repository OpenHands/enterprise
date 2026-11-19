import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { ReactNode } from "react";
import { useReleaseInfo } from "#/hooks/query/use-release-info";
import OptionService from "#/api/option-service/option-service.api";

vi.mock("#/api/option-service/option-service.api", () => ({
  default: {
    getReleaseInfo: vi.fn(),
  },
}));

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };
};

describe("useReleaseInfo", () => {
  it("returns the release/sdk version payload on success", async () => {
    vi.mocked(OptionService.getReleaseInfo).mockResolvedValue({
      app_version: "1.92.0",
      sdk_version: "1.62.0",
      enterprise_release: null,
    });

    const { result } = renderHook(() => useReleaseInfo(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual({
      app_version: "1.92.0",
      sdk_version: "1.62.0",
      enterprise_release: null,
    });
  });

  it("surfaces an error state when the request fails (older server/network error)", async () => {
    vi.mocked(OptionService.getReleaseInfo).mockRejectedValue(
      new Error("Network Error"),
    );

    const { result } = renderHook(() => useReleaseInfo(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true), {
      timeout: 5000,
    });
  });

  it("sets meta.disableToast so the global QueryCache error toast (see query-client-config.ts) is skipped", async () => {
    vi.mocked(OptionService.getReleaseInfo).mockRejectedValue(
      new Error("Network Error"),
    );

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(() => useReleaseInfo(), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    });

    await waitFor(() => expect(result.current.isError).toBe(true), {
      timeout: 5000,
    });

    const [query] = queryClient.getQueryCache().findAll();
    expect(query.meta).toEqual({ disableToast: true });
  });
});
