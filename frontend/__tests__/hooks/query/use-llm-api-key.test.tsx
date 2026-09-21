import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { useLlmApiKey } from "#/hooks/query/use-llm-api-key";
import OptionService from "#/api/option-service/option-service.api";
import { openHands } from "#/api/open-hands-axios";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";

describe("useLlmApiKey", () => {
  let queryClient: QueryClient;

  const createWrapper = () => {
    return ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
        },
      },
    });
    vi.clearAllMocks();
  });

  it("should fetch the key on self-hosted deployments with BYOR export enabled and no selected organization", async () => {
    const defaultConfig = createMockWebClientConfig();
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({
        app_mode: "oss",
        feature_flags: {
          ...defaultConfig.feature_flags,
          enable_byor_export: true,
        },
      }),
    );
    const getSpy = vi
      .spyOn(openHands, "get")
      .mockResolvedValue({ data: { key: "sk-self-hosted" } });
    useSelectedOrganizationStore.setState({ organizationId: null });

    const { result } = renderHook(() => useLlmApiKey(), {
      wrapper: createWrapper(),
    });

    await waitFor(() =>
      expect(result.current.data?.key).toBe("sk-self-hosted"),
    );
    expect(getSpy).toHaveBeenCalledWith("/api/keys/llm/byor");
  });
});
