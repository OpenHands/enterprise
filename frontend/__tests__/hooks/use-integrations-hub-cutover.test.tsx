import { renderHook, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useIntegrationsHubCutover } from "#/hooks/use-integrations-hub-cutover";
import { LEGACY_INTEGRATIONS_CUTOVER_STORAGE_KEY } from "#/components/features/integrations-hub/legacy-integrations-cutover";

const mockUseUserProviders = vi.hoisted(() =>
  vi.fn(() => ({
    providers: ["github", "gitlab"] as string[],
    isLoadingSettings: false,
  })),
);

const mockUseIntegrationsHub = vi.hoisted(() =>
  vi.fn(() => ({
    integrations: [{ slug: "slack", connected: true }],
    catalogIntegrations: [
      { slug: "github" },
      { slug: "slack" },
      { slug: "bitbucket" },
    ],
  })),
);

vi.mock("#/hooks/use-user-providers", () => ({
  useUserProviders: () => mockUseUserProviders(),
}));

vi.mock("#/hooks/query/use-integrations-hub", () => ({
  useIntegrationsHub: () => mockUseIntegrationsHub(),
}));

describe("useIntegrationsHubCutover", () => {
  beforeEach(() => {
    localStorage.clear();
    mockUseUserProviders.mockReturnValue({
      providers: ["github", "gitlab"],
      isLoadingSettings: false,
    });
  });

  it("opens with legacy items that still need reconnect", () => {
    const { result } = renderHook(() => useIntegrationsHubCutover());

    expect(result.current.isOpen).toBe(true);
    expect(result.current.items.map((item) => item.id)).toEqual([
      "github",
      "gitlab",
    ]);
    expect(result.current.items[0]?.canReconnectInHub).toBe(true);
    expect(result.current.items[1]?.canReconnectInHub).toBe(false);
  });

  it("stays closed while settings are loading", () => {
    mockUseUserProviders.mockReturnValue({
      providers: ["github"],
      isLoadingSettings: true,
    });

    const { result } = renderHook(() => useIntegrationsHubCutover());
    expect(result.current.isOpen).toBe(false);
    expect(result.current.isLoading).toBe(true);
  });

  it("persists dismissal in localStorage", () => {
    const { result } = renderHook(() => useIntegrationsHubCutover());

    act(() => {
      result.current.dismiss();
    });

    expect(result.current.isOpen).toBe(false);
    expect(localStorage.getItem(LEGACY_INTEGRATIONS_CUTOVER_STORAGE_KEY)).toBe(
      "true",
    );
  });
});
