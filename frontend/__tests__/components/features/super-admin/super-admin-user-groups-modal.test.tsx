import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SuperAdminUserGroupsModal } from "#/components/features/super-admin/super-admin-user-groups-modal";
import type { SuperAdminUserRow } from "#/components/features/super-admin/super-admin-types";
import { resetSuperAdminMockState } from "#/mocks/super-admin-handlers";

const { mutate, mutateStatus, mutateRemove } = vi.hoisted(() => ({
  mutate: vi.fn(),
  mutateStatus: vi.fn(),
  mutateRemove: vi.fn(),
}));

vi.mock("#/hooks/mutation/use-super-admin-mutations", () => ({
  useUpdateSuperAdminUserGroups: () => ({
    mutate,
    isPending: false,
  }),
  useUpdateSuperAdminUserStatus: () => ({
    mutate: mutateStatus,
    isPending: false,
  }),
  useRemoveSuperAdminUser: () => ({
    mutate: mutateRemove,
    isPending: false,
  }),
}));

async function chooseDropdownOption(
  events: ReturnType<typeof userEvent.setup>,
  testId: string,
  label: string,
) {
  const input = screen.getByTestId(testId).querySelector("input");
  if (!input) {
    throw new Error(`dropdown ${testId} has no input`);
  }
  await events.click(input);
  const options = await screen.findAllByRole("option");
  const option = options.find((node) => node.textContent === label);
  if (!option) {
    throw new Error(`dropdown option ${label} not found`);
  }
  await events.click(option);
}

const user: SuperAdminUserRow = {
  id: "99",
  name: "openhands",
  email: "me@acme.org",
  status: "active",
  memberships: [
    { orgId: "2", orgName: "Acme Corp", role: "owner", status: "active" },
    { orgId: "4", orgName: "All Hands AI", role: "admin", status: "inactive" },
  ],
};

describe("SuperAdminUserGroupsModal", () => {
  beforeEach(() => {
    mutate.mockClear();
    mutateStatus.mockClear();
    mutateRemove.mockClear();
  });

  it("suspends the account and deletes the user after confirmation", async () => {
    const events = userEvent.setup();
    const onClose = vi.fn();
    render(
      <SuperAdminUserGroupsModal
        user={user}
        organizations={[{ id: "2", name: "Acme Corp" }]}
        onClose={onClose}
      />,
    );

    await events.click(screen.getByTestId("super-admin-groups-modal-close"));
    expect(onClose).toHaveBeenCalledTimes(1);

    await events.click(screen.getByTestId("super-admin-user-suspend"));
    expect(mutateStatus).toHaveBeenCalledWith({
      userId: "99",
      status: "inactive",
    });

    await events.click(screen.getByTestId("super-admin-user-delete"));
    expect(mutateRemove).not.toHaveBeenCalled();
    await events.click(screen.getByTestId("super-admin-user-delete-confirm"));
    expect(mutateRemove).toHaveBeenCalledWith(
      { userId: "99" },
      { onSuccess: onClose },
    );
  });

  it("activates a suspended account", async () => {
    const events = userEvent.setup();
    render(
      <SuperAdminUserGroupsModal
        user={{ ...user, status: "inactive" }}
        organizations={[{ id: "2", name: "Acme Corp" }]}
        onClose={vi.fn()}
      />,
    );

    await events.click(screen.getByTestId("super-admin-user-activate"));
    expect(mutateStatus).toHaveBeenCalledWith({
      userId: "99",
      status: "active",
    });
  });

  it("does not let a super admin suspend or delete their own account", () => {
    // Act
    render(
      <SuperAdminUserGroupsModal
        user={user}
        organizations={[{ id: "2", name: "Acme Corp" }]}
        isSelf
        onClose={vi.fn()}
      />,
    );

    // Assert
    expect(screen.getByTestId("super-admin-user-suspend")).toBeDisabled();
    expect(screen.getByTestId("super-admin-user-delete")).toBeDisabled();
  });

  it("does not list the user's personal workspace among their groups", () => {
    // Arrange
    const withPersonalWorkspace: SuperAdminUserRow = {
      ...user,
      memberships: [
        {
          orgId: user.id,
          orgName: `user_${user.id}_org`,
          role: "owner",
          status: "active",
        },
        ...user.memberships,
      ],
    };

    // Act
    render(
      <SuperAdminUserGroupsModal
        user={withPersonalWorkspace}
        organizations={[{ id: "2", name: "Acme Corp" }]}
        onClose={vi.fn()}
      />,
    );

    // Assert
    expect(
      screen.queryByTestId(`super-admin-group-current-${user.id}`),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("super-admin-group-current-2"),
    ).toBeInTheDocument();
  });

  it("shows Bulk Actions as active once a group is checked", async () => {
    // Arrange
    const events = userEvent.setup();
    render(
      <SuperAdminUserGroupsModal
        user={user}
        organizations={[{ id: "2", name: "Acme Corp" }]}
        onClose={() => undefined}
      />,
    );
    const bulkInput = screen
      .getByTestId("super-admin-groups-bulk")
      .querySelector("input") as HTMLInputElement;
    expect(bulkInput).toBeDisabled();
    expect(bulkInput).not.toHaveClass("placeholder:text-white");

    // Act
    await events.click(screen.getByTestId("super-admin-group-current-2"));
    await events.click(screen.getByTestId("super-admin-group-current-4"));

    // Assert
    expect(bulkInput).toBeEnabled();
    expect(bulkInput).toHaveClass("placeholder:text-white");
  });

  it("suspends and removes only the checked groups", async () => {
    const events = userEvent.setup();
    render(
      <SuperAdminUserGroupsModal
        user={user}
        organizations={[
          { id: "2", name: "Acme Corp" },
          { id: "4", name: "All Hands AI" },
          { id: "5", name: "Northwind Labs" },
        ]}
        onClose={() => undefined}
      />,
    );

    await events.click(screen.getByTestId("super-admin-group-current-2"));
    await events.click(screen.getByTestId("super-admin-group-current-4"));
    await chooseDropdownOption(
      events,
      "super-admin-groups-bulk",
      "SUPER_ADMIN$SUSPEND",
    );

    expect(mutate).toHaveBeenCalledWith(
      {
        userId: "99",
        action: "suspend",
        orgIds: ["2", "4"],
      },
      expect.any(Object),
    );

    await chooseDropdownOption(
      events,
      "super-admin-groups-bulk",
      "SUPER_ADMIN$REMOVE",
    );
    await events.click(screen.getByTestId("super-admin-groups-remove-confirm"));
    expect(mutate).toHaveBeenLastCalledWith(
      {
        userId: "99",
        action: "remove",
        orgIds: ["2", "4"],
      },
      expect.any(Object),
    );
  });

  it("asks before removing the checked groups", async () => {
    // Arrange
    const events = userEvent.setup();
    render(
      <SuperAdminUserGroupsModal
        user={user}
        organizations={[{ id: "2", name: "Acme Corp" }]}
        onClose={() => undefined}
      />,
    );
    await events.click(screen.getByTestId("super-admin-group-current-2"));

    // Act
    await chooseDropdownOption(
      events,
      "super-admin-groups-bulk",
      "SUPER_ADMIN$REMOVE",
    );

    // Assert
    expect(
      screen.getByText("SUPER_ADMIN$REMOVE_MEMBERSHIPS_CONFIRM"),
    ).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("asks before removing a single group from its role menu", async () => {
    // Arrange
    const events = userEvent.setup();
    render(
      <SuperAdminUserGroupsModal
        user={user}
        organizations={[{ id: "2", name: "Acme Corp" }]}
        onClose={() => undefined}
      />,
    );

    // Act
    await events.click(
      screen
        .getByTestId("super-admin-group-current-role-2")
        .querySelector("input") as HTMLInputElement,
    );
    await events.click(
      screen.getByTestId("super-admin-group-current-role-2-remove"),
    );

    // Assert
    expect(
      screen.getByText("SUPER_ADMIN$REMOVE_MEMBERSHIPS_CONFIRM"),
    ).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("keeps the group when the removal is cancelled", async () => {
    // Arrange
    const events = userEvent.setup();
    render(
      <SuperAdminUserGroupsModal
        user={user}
        organizations={[{ id: "2", name: "Acme Corp" }]}
        onClose={() => undefined}
      />,
    );

    // Act
    await events.click(
      screen
        .getByTestId("super-admin-group-current-role-2")
        .querySelector("input") as HTMLInputElement,
    );
    await events.click(
      screen.getByTestId("super-admin-group-current-role-2-remove"),
    );
    await events.click(screen.getByTestId("super-admin-groups-remove-cancel"));

    // Assert
    expect(
      screen.queryByText("SUPER_ADMIN$REMOVE_MEMBERSHIPS_CONFIRM"),
    ).not.toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("changes one membership role from its dropdown", async () => {
    const events = userEvent.setup();
    render(
      <SuperAdminUserGroupsModal
        user={user}
        organizations={[{ id: "2", name: "Acme Corp" }]}
        onClose={() => undefined}
      />,
    );

    await chooseDropdownOption(
      events,
      "super-admin-group-current-role-4",
      "ORG$ROLE_MEMBER",
    );

    expect(mutate).toHaveBeenCalledWith(
      {
        userId: "99",
        action: "set_role",
        orgIds: ["4"],
        role: "member",
      },
      expect.any(Object),
    );

    expect(
      screen.getByTestId("super-admin-group-current-suspended-4"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("super-admin-group-current-suspended-2"),
    ).not.toBeInTheDocument();

    await events.click(
      screen
        .getByTestId("super-admin-group-current-role-2")
        .querySelector("input") as HTMLInputElement,
    );
    await events.click(
      screen.getByTestId("super-admin-group-current-role-2-status"),
    );
    expect(mutate).toHaveBeenCalledWith(
      {
        userId: "99",
        action: "suspend",
        orgIds: ["2"],
      },
      expect.any(Object),
    );

    await events.click(
      screen
        .getByTestId("super-admin-group-current-role-4")
        .querySelector("input") as HTMLInputElement,
    );
    await events.click(
      screen.getByTestId("super-admin-group-current-role-4-status"),
    );
    expect(mutate).toHaveBeenCalledWith(
      {
        userId: "99",
        action: "resume",
        orgIds: ["4"],
      },
      expect.any(Object),
    );

    await events.click(
      screen
        .getByTestId("super-admin-group-current-role-2")
        .querySelector("input") as HTMLInputElement,
    );
    await events.click(
      screen.getByTestId("super-admin-group-current-role-2-remove"),
    );
    await events.click(screen.getByTestId("super-admin-groups-remove-confirm"));
    expect(mutate).toHaveBeenLastCalledWith(
      {
        userId: "99",
        action: "remove",
        orgIds: ["2"],
      },
      expect.any(Object),
    );
  });

  it("adds a group from the add menu at the chosen role", async () => {
    const events = userEvent.setup();
    render(
      <SuperAdminUserGroupsModal
        user={user}
        organizations={[
          { id: "2", name: "Acme Corp" },
          { id: "5", name: "Northwind Labs" },
          { id: "3", name: "Beta LLC" },
        ]}
        onClose={() => undefined}
      />,
    );

    expect(
      screen.queryByTestId("super-admin-group-add-2"),
    ).not.toBeInTheDocument();

    await events.click(screen.getByTestId("super-admin-groups-add"));
    await events.click(screen.getByTestId("super-admin-group-add-5"));
    expect(screen.getByTestId("super-admin-group-add-5")).toBeInTheDocument();
    await events.click(
      screen.getByTestId("super-admin-group-add-role-5-member"),
    );

    expect(mutate).toHaveBeenCalledWith(
      {
        userId: "99",
        action: "add",
        orgIds: ["5"],
        role: "member",
      },
      expect.any(Object),
    );

    await events.click(screen.getByTestId("super-admin-group-add-3"));
    expect(screen.getByTestId("super-admin-group-add-3")).toBeInTheDocument();
    await events.click(
      screen.getByTestId("super-admin-group-add-role-3-admin"),
    );

    expect(mutate).toHaveBeenLastCalledWith(
      {
        userId: "99",
        action: "add",
        orgIds: ["3"],
        role: "admin",
      },
      expect.any(Object),
    );
  });

  it("updates only the selected organizations in the mock API", async () => {
    resetSuperAdminMockState();

    const suspend = await fetch(
      "http://localhost:3000/api/admin/directory/users/99/groups",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "suspend", org_ids: ["4"] }),
      },
    );
    expect(suspend.status).toBe(200);
    const suspended = (await suspend.json()) as {
      memberships: { org_id: string; status: string }[];
    };
    expect(
      suspended.memberships.find((membership) => membership.org_id === "4")
        ?.status,
    ).toBe("inactive");
    expect(
      suspended.memberships.find((membership) => membership.org_id === "2")
        ?.status,
    ).toBe("active");

    const removed = await fetch(
      "http://localhost:3000/api/admin/directory/users/99/groups",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "remove", org_ids: ["4"] }),
      },
    );
    expect(removed.status).toBe(200);
    const afterRemove = (await removed.json()) as {
      memberships: { org_id: string }[];
    };
    expect(
      afterRemove.memberships.some((membership) => membership.org_id === "4"),
    ).toBe(false);
    expect(
      afterRemove.memberships.some((membership) => membership.org_id === "2"),
    ).toBe(true);

    const blocked = await fetch(
      "http://localhost:3000/api/admin/directory/users/99/groups",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "remove", org_ids: ["2"] }),
      },
    );
    expect(blocked.status).toBe(409);
  });
});
