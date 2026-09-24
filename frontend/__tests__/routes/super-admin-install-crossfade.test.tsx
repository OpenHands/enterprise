import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRoutesStub } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";
import SuperAdminInstallLayout from "#/routes/super-admin-install-layout";
import SuperAdminInstallWelcome from "#/routes/super-admin-install-welcome";
import SuperAdminInstallTos from "#/routes/super-admin-install-tos";
import SuperAdminInstallAccount from "#/routes/super-admin-install-account";
import { resetSuperAdminNux } from "#/utils/org/super-admin-nux";

function renderInstall(initialPath = "/install") {
  const RouterStub = createRoutesStub([
    {
      path: "/install",
      Component: SuperAdminInstallLayout,
      children: [
        { index: true, Component: SuperAdminInstallWelcome },
        { path: "tos", Component: SuperAdminInstallTos },
        { path: "account", Component: SuperAdminInstallAccount },
      ],
    },
  ]);

  return render(<RouterStub initialEntries={[initialPath]} />);
}

describe("super admin install crossfade", () => {
  beforeEach(() => {
    resetSuperAdminNux();
  });

  it("keeps the welcome screen mounted while the terms screen fades in", async () => {
    const user = userEvent.setup();
    renderInstall();

    expect(
      screen.getByTestId("super-admin-install-welcome"),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("super-admin-install-crossfade")).toHaveLength(
      1,
    );

    await user.click(screen.getByTestId("sa-nux-welcome-next"));

    expect(screen.getByTestId("super-admin-install-tos")).toBeInTheDocument();
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
    expect(screen.getByTestId("super-admin-install-tos")).toBeInTheDocument();
    expect(screen.getAllByTestId("super-admin-install-crossfade")).toHaveLength(
      1,
    );
  });
});
