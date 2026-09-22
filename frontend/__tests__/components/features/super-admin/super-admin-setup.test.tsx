import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRoutesStub } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";
import { SuperAdminSetupGuide } from "#/components/features/super-admin/super-admin-setup-guide";
import {
  SUPER_ADMIN_SETUP_STEPS,
  SUPER_ADMIN_SETUP_STORAGE_KEY,
  getSuperAdminSetupState,
  resetSuperAdminSetupState,
  setSuperAdminSetupStepComplete,
} from "#/components/features/super-admin/super-admin-setup";

function renderSetup() {
  const RouterStub = createRoutesStub([
    {
      path: "/super-admin",
      Component: () => <div data-testid="dashboard-stub" />,
    },
    { path: "/super-admin/setup", Component: SuperAdminSetupGuide },
    { path: "/super-admin/instance", Component: () => <div /> },
  ]);
  return render(<RouterStub initialEntries={["/super-admin/setup"]} />);
}

describe("SuperAdminSetupGuide", () => {
  beforeEach(() => {
    window.localStorage.removeItem(SUPER_ADMIN_SETUP_STORAGE_KEY);
    resetSuperAdminSetupState();
  });

  it("starts with the first incomplete instance step as next", () => {
    renderSetup();

    expect(screen.getByTestId("super-admin-setup")).toBeInTheDocument();
    expect(
      screen.getAllByText("SUPER_ADMIN$SETUP_NEXT").length,
    ).toBeGreaterThan(0);
    expect(getSuperAdminSetupState().nextStep?.id).toBe("review-instance");
    expect(getSuperAdminSetupState().progress).toBe(0.4);
  });

  it("marks the next step complete and advances progress", async () => {
    const user = userEvent.setup();
    renderSetup();

    await user.click(
      screen.getByTestId("super-admin-setup-toggle-review-instance"),
    );

    expect(getSuperAdminSetupState().completed.has("review-instance")).toBe(
      true,
    );
    expect(getSuperAdminSetupState().nextStep?.id).toBe("grant-admins");
  });

  it("asks to remove the guide after the last step is completed", async () => {
    const user = userEvent.setup();
    SUPER_ADMIN_SETUP_STEPS.forEach((step) => {
      setSuperAdminSetupStepComplete(step.id, step.id !== "review-dashboard");
    });
    renderSetup();

    await user.click(
      screen.getByTestId("super-admin-setup-toggle-review-dashboard"),
    );

    expect(screen.getByTestId("confirmation-modal")).toHaveTextContent(
      "SUPER_ADMIN$SETUP_REMOVE_COMPLETE_CONFIRM",
    );

    await user.click(screen.getByTestId("confirm-button"));

    expect(getSuperAdminSetupState().visible).toBe(false);
    expect(screen.getByTestId("dashboard-stub")).toBeInTheDocument();
  });

  it("can remove the setup guide from the bottom of the page", async () => {
    const user = userEvent.setup();
    renderSetup();

    await user.click(screen.getByTestId("super-admin-setup-remove"));
    expect(screen.getByTestId("confirmation-modal")).toHaveTextContent(
      "SUPER_ADMIN$SETUP_REMOVE_CONFIRM",
    );

    await user.click(screen.getByTestId("confirm-button"));
    expect(getSuperAdminSetupState().visible).toBe(false);
  });
});
