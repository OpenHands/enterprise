import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Outlet, createRoutesStub, useNavigate } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  superAdminService,
  type SetupGuideSteps,
  type SetupState,
} from "#/api/super-admin-service/super-admin-service.api";
import { SuperAdminSetupFloatingWidget } from "#/components/features/super-admin/super-admin-setup-guide";
import { notifySuperAdminSetupStep } from "#/components/features/super-admin/super-admin-setup";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import { SUPER_ADMIN_QUERY_KEYS } from "#/hooks/query/use-super-admin";
import { resetSuperAdminNux } from "#/utils/org/super-admin-nux";
import { stopGuidedTour } from "#/components/features/setup/tours/tour-engine";

vi.mock("#/hooks/query/use-me", () => ({
  useMe: () => ({
    data: { permissions: ["manage_super_admins", "create_organization"] },
  }),
}));

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => ({
    data: { feature_flags: { enable_super_admin: true } },
  }),
}));

const NO_STEPS_DONE: SetupGuideSteps = {
  org_llm: false,
  mcp_server: false,
  automation: false,
  invite: false,
};

const GUIDE_STATE: SetupState = {
  wizard_pending: false,
  guide_org_id: "guide-org",
  guide_dismissed: false,
  guide_steps: NO_STEPS_DONE,
};

function Jump({ to, label }: { to: string; label: string }) {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      data-testid={`go-${label}`}
      onClick={() => navigate(to)}
    >
      {label}
    </button>
  );
}

function renderWidget(initialPath: string, state: SetupState = GUIDE_STATE) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  // Server state already loaded; any refetch reads the spied service.
  queryClient.setQueryData(SUPER_ADMIN_QUERY_KEYS.setupState, state);
  const RouterStub = createRoutesStub([
    {
      path: "/super-admin",
      Component: () => (
        <>
          <SuperAdminSetupFloatingWidget />
          <Outlet />
        </>
      ),
      children: [
        {
          path: "setup",
          Component: () => (
            <Jump to={SUPER_ADMIN_PATHS.organizations} label="orgs" />
          ),
        },
        {
          path: "organizations",
          Component: () => (
            <Jump to={SUPER_ADMIN_PATHS.instance} label="instance" />
          ),
        },
        {
          path: "instance",
          Component: () => <Jump to={SUPER_ADMIN_PATHS.setup} label="setup" />,
        },
      ],
    },
  ]);

  return render(
    <QueryClientProvider client={queryClient}>
      <RouterStub initialEntries={[initialPath]} />
    </QueryClientProvider>,
  );
}

describe("SuperAdminSetupFloatingWidget", () => {
  beforeEach(() => {
    // The guide no longer depends on this browser having walked the wizard.
    resetSuperAdminNux();
    stopGuidedTour();
    vi.spyOn(superAdminService, "getSetupState").mockResolvedValue(GUIDE_STATE);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts closed on the setup guide and opens after leaving that page", async () => {
    const user = userEvent.setup();
    renderWidget(SUPER_ADMIN_PATHS.setup);

    expect(
      screen.getByTestId("super-admin-setup-floating-toggle"),
    ).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByTestId("super-admin-setup-floating-panel"),
    ).not.toBeInTheDocument();

    await user.click(screen.getByTestId("go-orgs"));

    expect(
      screen.getByTestId("super-admin-setup-floating-panel"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("super-admin-setup-floating-toggle"),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("stays closed on other pages after the user dismisses it", async () => {
    const user = userEvent.setup();
    renderWidget(SUPER_ADMIN_PATHS.organizations);

    expect(
      screen.getByTestId("super-admin-setup-floating-panel"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "BUTTON$CLOSE" }));
    expect(
      screen.queryByTestId("super-admin-setup-floating-panel"),
    ).not.toBeInTheDocument();

    await user.click(screen.getByTestId("go-instance"));
    expect(
      screen.queryByTestId("super-admin-setup-floating-panel"),
    ).not.toBeInTheDocument();

    await user.click(screen.getByTestId("go-setup"));
    expect(
      screen.queryByTestId("super-admin-setup-floating-panel"),
    ).not.toBeInTheDocument();

    await user.click(screen.getByTestId("super-admin-setup-floating-toggle"));
    expect(
      screen.getByTestId("super-admin-setup-floating-panel"),
    ).toBeInTheDocument();
  });

  it("shows progress from the server and re-reads it on the next page", async () => {
    // Arrange
    const user = userEvent.setup();
    renderWidget(SUPER_ADMIN_PATHS.organizations);
    expect(screen.getByText("0/4")).toBeInTheDocument();
    vi.mocked(superAdminService.getSetupState).mockResolvedValue({
      ...GUIDE_STATE,
      guide_steps: { ...NO_STEPS_DONE, org_llm: true },
    });

    // Act
    await user.click(screen.getByTestId("go-instance"));

    // Assert
    expect(await screen.findByText("1/4")).toBeInTheDocument();
  });

  it("re-reads progress when a step's action reports success on the same page", async () => {
    // Arrange
    renderWidget(SUPER_ADMIN_PATHS.organizations);
    expect(screen.getByText("0/4")).toBeInTheDocument();
    vi.mocked(superAdminService.getSetupState).mockResolvedValue({
      ...GUIDE_STATE,
      guide_steps: { ...NO_STEPS_DONE, mcp_server: true },
    });

    // Act
    act(() => notifySuperAdminSetupStep("add-integration"));

    // Assert
    expect(await screen.findByText("1/4")).toBeInTheDocument();
  });

  it("opens Agent Canvas's MCP page with a page load for Add an integration", async () => {
    // Arrange
    const replace = vi.fn();
    vi.stubGlobal("location", { ...window.location, replace });
    const user = userEvent.setup();
    renderWidget(SUPER_ADMIN_PATHS.organizations);

    // Act
    await user.click(
      within(
        screen.getByTestId("super-admin-setup-floating-step-add-integration"),
      ).getByRole("button", { name: /SUPER_ADMIN\$SETUP_STEP_INTEGRATION/ }),
    );

    // Assert
    expect(replace).toHaveBeenCalledWith("/canvas/mcp");
    vi.unstubAllGlobals();
  });

  it("is not shown to a user the server gives no guide", () => {
    // Act
    renderWidget(SUPER_ADMIN_PATHS.organizations, {
      wizard_pending: false,
      guide_org_id: null,
      guide_dismissed: false,
      guide_steps: null,
    });

    // Assert
    expect(
      screen.queryByTestId("super-admin-setup-floating"),
    ).not.toBeInTheDocument();
  });
});
