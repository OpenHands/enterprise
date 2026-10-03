import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, createRoutesStub, useNavigate } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SuperAdminSetupFloatingWidget } from "#/components/features/super-admin/super-admin-setup-guide";
import {
  SUPER_ADMIN_SETUP_STORAGE_KEY,
  resetSuperAdminSetupState,
} from "#/components/features/super-admin/super-admin-setup";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import {
  resetSuperAdminNux,
  setSuperAdminNuxStep,
} from "#/utils/org/super-admin-nux";
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

function renderWidget(initialPath: string) {
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

  return render(<RouterStub initialEntries={[initialPath]} />);
}

describe("SuperAdminSetupFloatingWidget", () => {
  beforeEach(() => {
    window.localStorage.removeItem(SUPER_ADMIN_SETUP_STORAGE_KEY);
    resetSuperAdminSetupState();
    resetSuperAdminNux();
    setSuperAdminNuxStep("done");
    stopGuidedTour();
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
});
