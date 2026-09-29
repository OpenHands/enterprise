import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ConfigService from "#/api/config-service/config-service.api";
import { useHydrateFreeModels } from "#/hooks/query/use-free-models";

const wait = (ms: number) =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });

const mockState = vi.hoisted(() => ({
  isAuthenticated: false as boolean | undefined,
  appMode: "saas" as string | undefined,
  organizationId: "org-1" as string | null,
  isOnIntermediatePage: false,
}));

vi.mock("#/api/config-service/config-service.api", () => ({
  default: {
    searchModels: vi.fn().mockResolvedValue({
      items: [
        { provider: "openhands", name: "free-x", free: true, verified: true },
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

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useHydrateFreeModels auth gating", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockState.isAuthenticated = false;
    mockState.appMode = "saas";
    mockState.organizationId = "org-1";
    mockState.isOnIntermediatePage = false;
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

  it("fetches models once authed (OSS, no org required)", async () => {
    mockState.isAuthenticated = true;
    mockState.appMode = "oss";
    mockState.organizationId = null;
    renderHook(() => useHydrateFreeModels(), { wrapper });
    await waitFor(() => {
      expect(ConfigService.searchModels).toHaveBeenCalledTimes(1);
    });
  });

  it("fetches models once authed with an org (SaaS)", async () => {
    mockState.isAuthenticated = true;
    mockState.appMode = "saas";
    mockState.organizationId = "org-1";
    renderHook(() => useHydrateFreeModels(), { wrapper });
    await waitFor(() => {
      expect(ConfigService.searchModels).toHaveBeenCalledTimes(1);
    });
  });
});
