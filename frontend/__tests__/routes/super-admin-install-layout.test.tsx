import { render, screen } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OptionService from "#/api/option-service/option-service.api";
import { superAdminService } from "#/api/super-admin-service/super-admin-service.api";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { queryClient } from "#/query-client-config";
import SuperAdminInstallLayout, {
  clientLoader,
} from "#/routes/super-admin-install-layout";
import SuperAdminInstallWelcome from "#/routes/super-admin-install-welcome";
import {
  getSuperAdminNuxStep,
  resetSuperAdminNux,
  setSuperAdminNuxStep,
} from "#/utils/org/super-admin-nux";

function mockSuperAdminFlag(enabled: boolean) {
  vi.spyOn(OptionService, "getConfig").mockResolvedValue(
    createMockWebClientConfig({
      app_mode: "saas",
      feature_flags: {
        ...createMockWebClientConfig().feature_flags,
        enable_super_admin: enabled,
      },
    }),
  );
}

function mockWizardPending(pending: boolean) {
  vi.spyOn(superAdminService, "getSetupState").mockResolvedValue({
    wizard_pending: pending,
    guide_org_id: null,
    guide_dismissed: false,
  });
}

function renderInstall() {
  const RouterStub = createRoutesStub([
    { path: "/", Component: () => <div data-testid="home-screen" /> },
    {
      path: "/super-admin/setup",
      Component: () => <div data-testid="setup-guide-screen" />,
    },
    {
      path: "/install",
      Component: SuperAdminInstallLayout,
      loader: clientLoader,
      children: [{ index: true, Component: SuperAdminInstallWelcome }],
    },
  ]);

  return render(<RouterStub initialEntries={["/install"]} />);
}

describe("super admin install layout", () => {
  beforeEach(() => {
    queryClient.clear();
    resetSuperAdminNux();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sends the user away from the install wizard when the Super Admin flag is off", async () => {
    mockSuperAdminFlag(false);

    renderInstall();

    expect(await screen.findByTestId("home-screen")).toBeInTheDocument();
    expect(
      screen.queryByTestId("super-admin-install-welcome"),
    ).not.toBeInTheDocument();
  });

  it("shows the install wizard when the Super Admin flag is on", async () => {
    mockSuperAdminFlag(true);

    renderInstall();

    expect(
      await screen.findByTestId("super-admin-install-welcome"),
    ).toBeInTheDocument();
  });

  it("sends the user away when the server has no pending wizard for them", async () => {
    // Arrange
    mockSuperAdminFlag(true);
    mockWizardPending(false);

    // Act
    renderInstall();

    // Assert
    expect(await screen.findByTestId("home-screen")).toBeInTheDocument();
    expect(
      screen.queryByTestId("super-admin-install-welcome"),
    ).not.toBeInTheDocument();
  });

  it("restarts the wizard when this browser holds finished progress the server does not", async () => {
    // Arrange
    mockSuperAdminFlag(true);
    mockWizardPending(true);
    setSuperAdminNuxStep("done");

    // Act
    renderInstall();

    // Assert
    expect(
      await screen.findByTestId("super-admin-install-welcome"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("setup-guide-screen")).not.toBeInTheDocument();
    expect(getSuperAdminNuxStep()).toBe("welcome");
  });
});
