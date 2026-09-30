import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SuperAdminUserMemberships } from "#/components/features/super-admin/super-admin-chrome";
import { SUPER_ADMIN_USERS } from "#/components/features/super-admin/super-admin-mock";

describe("Super Admin user memberships", () => {
  it("lists each org and its role for a user in more than one organization", () => {
    const user = SUPER_ADMIN_USERS[0];

    const { rerender } = render(
      <SuperAdminUserMemberships
        memberships={user.memberships}
        field="orgName"
      />,
    );

    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getByText("All Hands AI")).toBeInTheDocument();

    rerender(
      <SuperAdminUserMemberships memberships={user.memberships} field="role" />,
    );

    expect(screen.getByText("owner")).toBeInTheDocument();
    expect(screen.getByText("admin")).toBeInTheDocument();
  });

  it("lets a super admin open an org from a membership row", async () => {
    const user = userEvent.setup();
    const onOrgClick = vi.fn();
    const memberships = SUPER_ADMIN_USERS[3].memberships;

    render(
      <SuperAdminUserMemberships
        memberships={memberships}
        field="orgName"
        onOrgClick={onOrgClick}
      />,
    );

    await user.click(screen.getByText("Northwind Labs"));

    expect(onOrgClick).toHaveBeenCalledWith("5", "Northwind Labs");
  });
});
