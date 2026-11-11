import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "test-utils";
import { licenseService } from "#/api/license-service/license-service.api";
import SuperAdminInstallLayout from "#/routes/super-admin-install-layout";
import SuperAdminInstallWelcome from "#/routes/super-admin-install-welcome";
import SuperAdminInstallLicense from "#/routes/super-admin-install-license";
import { resetSuperAdminNux } from "#/utils/org/super-admin-nux";

function renderInstall(initialPath = "/install") {
  const RouterStub = createRoutesStub([
    {
      path: "/install",
      Component: SuperAdminInstallLayout,
      children: [
        { index: true, Component: SuperAdminInstallWelcome },
        { path: "license", Component: SuperAdminInstallLicense },
      ],
    },
  ]);

  return renderWithProviders(<RouterStub initialEntries={[initialPath]} />);
}

describe("super admin install crossfade", () => {
  beforeEach(() => {
    resetSuperAdminNux();
    vi.spyOn(licenseService, "getLicense").mockResolvedValue({
      source: null,
      kind: null,
      key_hint: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps the welcome screen mounted while the license screen fades in", async () => {
    const user = userEvent.setup();
    renderInstall();

    expect(
      screen.getByTestId("super-admin-install-welcome"),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("super-admin-install-crossfade")).toHaveLength(
      1,
    );

    await user.click(screen.getByTestId("sa-nux-welcome-next"));

    expect(
      screen.getByTestId("super-admin-install-license"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("super-admin-install-welcome"),
    ).toBeInTheDocument();
    expect(
      screen.getAllByTestId("super-admin-install-crossfade").length,
    ).toBeGreaterThan(1);

    await waitFor(() => {
      expect(
        screen.queryByTestId("super-admin-install-welcome"),
      ).not.toBeInTheDocument();
    });
    expect(
      screen.getByTestId("super-admin-install-license"),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("super-admin-install-crossfade")).toHaveLength(
      1,
    );
  });
});
