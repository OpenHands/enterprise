import {
  act,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactNode } from "react";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OptionService from "#/api/option-service/option-service.api";
import {
  superAdminService,
  type SetupGuideSteps,
  type SetupState,
} from "#/api/super-admin-service/super-admin-service.api";
import { SuperAdminSetupGuide } from "#/components/features/super-admin/super-admin-setup-guide";
import { useSuperAdminSetup } from "#/components/features/super-admin/super-admin-setup";
import { SUPER_ADMIN_QUERY_KEYS } from "#/hooks/query/use-super-admin";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";

const NO_STEPS_DONE: SetupGuideSteps = {
  org_llm: false,
  mcp_server: false,
  automation: false,
  invite: false,
};

const ALL_STEPS_DONE: SetupGuideSteps = {
  org_llm: true,
  mcp_server: true,
  automation: true,
  invite: true,
};

function guideState(guideSteps: SetupGuideSteps): SetupState {
  return {
    wizard_pending: false,
    guide_org_id: "guide-org",
    guide_dismissed: false,
    guide_steps: guideSteps,
  };
}

/** Server state already loaded, and returned again on any refetch. */
function createQueryClient(state: SetupState) {
  vi.spyOn(superAdminService, "getSetupState").mockResolvedValue(state);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  queryClient.setQueryData(SUPER_ADMIN_QUERY_KEYS.setupState, state);
  return queryClient;
}

function renderSetupHook(state: SetupState) {
  const queryClient = createQueryClient(state);
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  }
  return renderHook(() => useSuperAdminSetup(), { wrapper: Wrapper });
}

function renderSetupPage(state: SetupState) {
  const queryClient = createQueryClient(state);
  const RouterStub = createRoutesStub([
    {
      path: "/super-admin",
      Component: () => <div data-testid="dashboard-stub" />,
    },
    { path: "/super-admin/setup", Component: SuperAdminSetupGuide },
    {
      path: "/automations/templates",
      Component: () => <div data-testid="automation-templates-stub" />,
    },
  ]);
  render(
    <QueryClientProvider client={queryClient}>
      <RouterStub initialEntries={["/super-admin/setup"]} />
    </QueryClientProvider>,
  );
  return queryClient;
}

beforeEach(() => {
  vi.spyOn(OptionService, "getConfig").mockResolvedValue(
    createMockWebClientConfig({
      feature_flags: {
        ...createMockWebClientConfig().feature_flags,
        enable_super_admin: true,
      },
    }),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useSuperAdminSetup", () => {
  it("marks done only the steps the server reports for the guide's organization", () => {
    // Arrange
    const state = guideState({ ...NO_STEPS_DONE, org_llm: true });

    // Act
    const { result } = renderSetupHook(state);

    // Assert
    expect([...result.current.completed]).toEqual(["add-llm"]);
    expect(result.current.nextStep?.id).toBe("first-automation");
    expect(result.current.completedCount).toBe(1);
    expect(result.current.totalCount).toBe(4);
    expect(result.current.visible).toBe(true);
  });

  it("finishes the guide without the optional SAML step and stops showing it", () => {
    // Arrange
    const state = guideState(ALL_STEPS_DONE);

    // Act
    const { result } = renderSetupHook(state);

    // Assert
    expect(result.current.completed.has("optional-saml")).toBe(false);
    expect(result.current.nextStep).toBeNull();
    expect(result.current.progress).toBe(1);
    expect(result.current.visible).toBe(false);
  });

  it.each([
    {
      who: "a user the server gives no guide",
      state: {
        wizard_pending: false,
        guide_org_id: null,
        guide_dismissed: false,
        guide_steps: null,
      },
    },
    {
      who: "a first Super Admin who dismissed the guide",
      state: {
        wizard_pending: false,
        guide_org_id: "guide-org",
        guide_dismissed: true,
        guide_steps: null,
      },
    },
  ])("does not show the guide to $who", ({ state }) => {
    // Act
    const { result } = renderSetupHook(state);

    // Assert
    expect(result.current.active).toBe(false);
    expect(result.current.visible).toBe(false);
    expect(result.current.completedCount).toBe(0);
  });
});

describe("SuperAdminSetupGuide", () => {
  it("saves the dismissal on the server when the guide is removed", async () => {
    // Arrange
    const updateSetupState = vi
      .spyOn(superAdminService, "updateSetupState")
      .mockResolvedValue({
        ...guideState(NO_STEPS_DONE),
        guide_dismissed: true,
        guide_steps: null,
      });
    const user = userEvent.setup();
    renderSetupPage(guideState(NO_STEPS_DONE));

    // Act
    await user.click(screen.getByTestId("super-admin-setup-remove"));
    await user.click(screen.getByTestId("confirm-button"));

    // Assert
    expect(updateSetupState).toHaveBeenCalledWith({ guide_dismissed: true });
    expect(await screen.findByTestId("dashboard-stub")).toBeInTheDocument();
  });

  it("asks to remove the guide when the server reports the last step done", async () => {
    // Arrange
    const queryClient = renderSetupPage(
      guideState({ ...ALL_STEPS_DONE, invite: false }),
    );

    // Act
    act(() => {
      queryClient.setQueryData(
        SUPER_ADMIN_QUERY_KEYS.setupState,
        guideState(ALL_STEPS_DONE),
      );
    });

    // Assert
    await waitFor(() =>
      expect(screen.getByTestId("confirmation-modal")).toHaveTextContent(
        "SUPER_ADMIN$SETUP_REMOVE_COMPLETE_CONFIRM",
      ),
    );
  });

  it("opens the template page instead of a tour when the next step is in Agent Canvas", async () => {
    // Arrange
    const user = userEvent.setup();
    renderSetupPage(guideState({ ...NO_STEPS_DONE, org_llm: true }));

    // Act
    await user.click(screen.getByTestId("super-admin-setup-start-guide"));

    // Assert
    expect(
      await screen.findByTestId("automation-templates-stub"),
    ).toBeInTheDocument();
  });

  describe("Add an integration", () => {
    // The step lives in Agent Canvas, so it must open with a page load.
    const replace = vi.fn();

    beforeEach(() => {
      replace.mockReset();
      vi.stubGlobal("location", { ...window.location, replace });
    });

    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it("opens Agent Canvas's MCP page instead of a tour when it is the next step", async () => {
      // Arrange
      const user = userEvent.setup();
      renderSetupPage(
        guideState({ ...NO_STEPS_DONE, org_llm: true, automation: true }),
      );

      // Act
      await user.click(screen.getByTestId("super-admin-setup-start-guide"));

      // Assert
      await waitFor(() => expect(replace).toHaveBeenCalledWith("/canvas/mcp"));
    });

    it("opens Agent Canvas's MCP page from its step", async () => {
      // Arrange
      const user = userEvent.setup();
      renderSetupPage(guideState(NO_STEPS_DONE));

      // Act
      await user.click(
        within(
          screen.getByTestId("super-admin-setup-step-add-integration"),
        ).getByRole("button", { name: /SUPER_ADMIN\$SETUP_STEP_INTEGRATION/ }),
      );

      // Assert
      expect(replace).toHaveBeenCalledWith("/canvas/mcp");
    });
  });

  it("does not ask again when an already finished guide is opened", () => {
    // Act
    renderSetupPage(guideState(ALL_STEPS_DONE));

    // Assert
    expect(screen.getByTestId("super-admin-setup")).toBeInTheDocument();
    expect(screen.queryByTestId("confirmation-modal")).not.toBeInTheDocument();
  });
});
