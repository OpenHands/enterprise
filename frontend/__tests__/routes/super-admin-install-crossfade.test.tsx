import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRoutesStub } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";
import { renderWithProviders } from "test-utils";
import SuperAdminInstallLayout from "#/routes/super-admin-install-layout";
import SuperAdminInstallWelcome from "#/routes/super-admin-install-welcome";
import SuperAdminInstallCompany from "#/routes/super-admin-install-company";
import { resetSuperAdminNux } from "#/utils/org/super-admin-nux";

function renderInstall(initialPath = "/install") {
  const RouterStub = createRoutesStub([
    {
      path: "/install",
      Component: SuperAdminInstallLayout,
      children: [
        { index: true, Component: SuperAdminInstallWelcome },
        { path: "company", Component: SuperAdminInstallCompany },
      ],
    },
  ]);

  return renderWithProviders(<RouterStub initialEntries={[initialPath]} />);
}

describe("super admin install crossfade", () => {
  beforeEach(() => {
    resetSuperAdminNux();
  });

  it("keeps the welcome screen mounted while the company screen fades in", async () => {
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
      screen.getByTestId("super-admin-install-company"),
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
      screen.getByTestId("super-admin-install-company"),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("super-admin-install-crossfade")).toHaveLength(
      1,
    );
  });
});
