import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { ReactNode } from "react";
import { useServerInfo } from "#/hooks/query/use-server-info";
import OptionService from "#/api/option-service/option-service.api";

vi.mock("#/api/option-service/option-service.api", () => ({
  default: {
    getServerInfo: vi.fn(),
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

describe("useServerInfo", () => {
  it("returns the server/sdk version payload on success", async () => {
    vi.mocked(OptionService.getServerInfo).mockResolvedValue({
      app_version: "1.92.0",
      sdk_version: "1.62.0",
      enterprise_release: null,
    });

    const { result } = renderHook(() => useServerInfo(), {
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
    vi.mocked(OptionService.getServerInfo).mockRejectedValue(
      new Error("Network Error"),
    );

    const { result } = renderHook(() => useServerInfo(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true), {
      timeout: 5000,
    });
  });
});
