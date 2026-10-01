import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  SuperAdminRowMenu,
  SuperAdminTable,
  SuperAdminUserMemberships,
} from "#/components/features/super-admin/super-admin-chrome";
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

  it("opens a user from the row without stealing the org or menu click", async () => {
    const user = userEvent.setup();
    const onRowClick = vi.fn();
    const onOrgClick = vi.fn();
    const onMenu = vi.fn();
    const row = SUPER_ADMIN_USERS[0];

    render(
      <SuperAdminTable
        testId="super-admin-users-table"
        rows={[row]}
        getRowKey={(item) => item.id}
        empty="empty"
        onRowClick={onRowClick}
        columns={[
          {
            key: "name",
            header: "Name",
            render: (item) => item.name,
          },
          {
            key: "org",
            header: "Organizations",
            render: (item) => (
              <SuperAdminUserMemberships
                memberships={item.memberships}
                field="orgName"
                onOrgClick={onOrgClick}
              />
            ),
          },
          {
            key: "actions",
            header: "",
            render: () => (
              <SuperAdminRowMenu
                testId="row-menu"
                ariaLabel="Row actions"
                items={[{ label: "Manage user", onSelect: onMenu }]}
              />
            ),
          },
        ]}
      />,
    );

    await user.click(screen.getByText(row.name));
    expect(onRowClick).toHaveBeenCalledWith(row);

    await user.click(screen.getByText("Acme Corp"));
    expect(onOrgClick).toHaveBeenCalledWith("2", "Acme Corp");
    expect(onRowClick).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Row actions" }));
    expect(onMenu).not.toHaveBeenCalled();
    expect(onRowClick).toHaveBeenCalledTimes(1);
  });
});
