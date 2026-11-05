import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ConfigService from "#/api/config-service/config-service.api";
import { useHydrateFreeModels } from "#/hooks/query/use-free-models";
import { useFreeModelsStore } from "#/stores/free-models-store";

const wait = (ms: number) =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });

const mockState = vi.hoisted(() => ({
  isAuthenticated: false as boolean | undefined,
  appMode: "saas" as string | undefined,
  organizationId: "org-1" as string | null,
  isOnIntermediatePage: false,
  hostedDomain: true,
}));

vi.mock("#/api/config-service/config-service.api", () => ({
  default: {
    searchModels: vi.fn().mockResolvedValue({
      items: [
        { provider: "openhands", name: "free-x", free: true, verified: true },
        {
          provider: "openhands",
          name: "default-y",
          default: true,
          verified: true,
        },
      ],
      next_page_id: null,
    }),
  },
}));

vi.mock("#/hooks/query/use-is-authed", () => ({
  useIsAuthed: () => ({ data: mockState.isAuthenticated }),
}));

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => ({ data: { app_mode: mockState.appMode } }),
}));

vi.mock("#/hooks/use-is-on-intermediate-page", () => ({
  useIsOnIntermediatePage: () => mockState.isOnIntermediatePage,
}));

vi.mock("#/hooks/use-org-type-and-access", () => ({
  useOrgTypeAndAccess: () => ({ organizationId: mockState.organizationId }),
}));

vi.mock("#/utils/domain-gate", () => ({
  isOpenHandsHostedDomain: () => mockState.hostedDomain,
}));

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function resetStore() {
  useFreeModelsStore.setState({
    freeModels: new Set<string>(),
    defaultModel: null,
    defaultModelReady: false,
  });
}

describe("useHydrateFreeModels auth gating", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockState.isAuthenticated = false;
    mockState.appMode = "saas";
    mockState.organizationId = "org-1";
    mockState.isOnIntermediatePage = false;
    mockState.hostedDomain = true;
    resetStore();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not fetch models when the user is not authenticated", async () => {
    mockState.isAuthenticated = false;
    const { unmount } = renderHook(() => useHydrateFreeModels(), { wrapper });
    await wait(50);
    expect(ConfigService.searchModels).not.toHaveBeenCalled();
    unmount();
  });

  it("does not fetch models on an intermediate page even when authed", async () => {
    mockState.isAuthenticated = true;
    mockState.isOnIntermediatePage = true;
    const { unmount } = renderHook(() => useHydrateFreeModels(), { wrapper });
    await wait(50);
    expect(ConfigService.searchModels).not.toHaveBeenCalled();
    unmount();
  });

  it("does not fetch models in SaaS mode until an org is selected", async () => {
    mockState.isAuthenticated = true;
    mockState.appMode = "saas";
    mockState.organizationId = null;
    const { unmount } = renderHook(() => useHydrateFreeModels(), { wrapper });
    await wait(50);
    expect(ConfigService.searchModels).not.toHaveBeenCalled();
    unmount();
  });

  it("does not fetch models on a non-hosted domain", async () => {
    mockState.isAuthenticated = true;
    mockState.hostedDomain = false;
    const { unmount } = renderHook(() => useHydrateFreeModels(), { wrapper });
    await wait(50);
    expect(ConfigService.searchModels).not.toHaveBeenCalled();
    unmount();
  });

  it("fetches models once authed (OSS, no org required)", async () => {
    mockState.isAuthenticated = true;
    mockState.appMode = "oss";
    mockState.organizationId = null;
    renderHook(() => useHydrateFreeModels(), { wrapper });
    await waitFor(() => {
      expect(ConfigService.searchModels).toHaveBeenCalledTimes(1);
    });
  });

  it("fetches models once authed with an org (SaaS) and mirrors the flags into the store", async () => {
    mockState.isAuthenticated = true;
    mockState.appMode = "saas";
    mockState.organizationId = "org-1";
    renderHook(() => useHydrateFreeModels(), { wrapper });

    await waitFor(() => {
      expect(ConfigService.searchModels).toHaveBeenCalledTimes(1);
    });

    // The store keys full ``openhands/<name>`` ids, which is what the
    // `useFreeModels` consumers (e.g. the ProfileRow free label) match on.
    await waitFor(() => {
      expect(useFreeModelsStore.getState().freeModels).toEqual(
        new Set(["openhands/free-x"]),
      );
      expect(useFreeModelsStore.getState().defaultModel).toBe(
        "openhands/default-y",
      );
    });
  });
});
