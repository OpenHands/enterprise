import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import OptionService from "#/api/option-service/option-service.api";
import {
  superAdminService,
  type SetupState,
} from "#/api/super-admin-service/super-admin-service.api";
import { useUpdateSetupState } from "#/hooks/mutation/use-super-admin-mutations";
import { useConfig } from "#/hooks/query/use-config";
import { useSetupState } from "#/hooks/query/use-super-admin";

const PENDING_SETUP_STATE: SetupState = {
  wizard_pending: true,
  guide_org_id: null,
  guide_dismissed: false,
  guide_steps: null,
};

function mockSuperAdminFlag(enabled: boolean) {
  vi.spyOn(OptionService, "getConfig").mockResolvedValue({
    app_mode: "saas",
    feature_flags: { enable_super_admin: enabled },
  } as Awaited<ReturnType<typeof OptionService.getConfig>>);
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  }
  return Wrapper;
}

describe("useSetupState", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the signed-in user's setup state when the Super Admin flag is on", async () => {
    // Arrange
    mockSuperAdminFlag(true);
    vi.spyOn(superAdminService, "getSetupState").mockResolvedValue(
      PENDING_SETUP_STATE,
    );

    // Act
    const { result } = renderHook(() => useSetupState(), {
      wrapper: createWrapper(),
    });

    // Assert
    await waitFor(() =>
      expect(result.current.data).toEqual(PENDING_SETUP_STATE),
    );
  });

  it("does not request the setup state when the Super Admin flag is off", async () => {
    // Arrange
    mockSuperAdminFlag(false);
    const getSetupState = vi.spyOn(superAdminService, "getSetupState");

    // Act
    const { result } = renderHook(
      () => ({ config: useConfig(), setupState: useSetupState() }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(result.current.config.isSuccess).toBe(true));

    // Assert
    expect(getSetupState).not.toHaveBeenCalled();
    expect(result.current.setupState.data).toBeUndefined();
  });

  it("returns the saved state after an update", async () => {
    // Arrange
    mockSuperAdminFlag(true);
    vi.spyOn(superAdminService, "getSetupState").mockResolvedValue(
      PENDING_SETUP_STATE,
    );
    const savedState = { ...PENDING_SETUP_STATE, wizard_pending: false };
    const updateSetupState = vi
      .spyOn(superAdminService, "updateSetupState")
      .mockResolvedValue(savedState);
    const { result } = renderHook(
      () => ({ setupState: useSetupState(), update: useUpdateSetupState() }),
      { wrapper: createWrapper() },
    );
    await waitFor(() =>
      expect(result.current.setupState.data).toEqual(PENDING_SETUP_STATE),
    );

    // Act
    await act(async () => {
      await result.current.update.mutateAsync({ wizard_completed: true });
    });

    // Assert
    expect(updateSetupState).toHaveBeenCalledWith({ wizard_completed: true });
    await waitFor(() =>
      expect(result.current.setupState.data).toEqual(savedState),
    );
  });
});
