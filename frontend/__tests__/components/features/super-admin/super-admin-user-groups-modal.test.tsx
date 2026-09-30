import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SuperAdminUserGroupsModal } from "#/components/features/super-admin/super-admin-user-groups-modal";
import type { SuperAdminUserRow } from "#/components/features/super-admin/super-admin-mock";
import { resetSuperAdminMockState } from "#/mocks/super-admin-handlers";

const { mutate } = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("#/hooks/mutation/use-super-admin-mutations", () => ({
  useUpdateSuperAdminUserGroups: () => ({
    mutate,
    isPending: false,
  }),
}));

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
    await events.click(screen.getByTestId("super-admin-groups-suspend"));

    expect(mutate).toHaveBeenCalledWith(
      {
        userId: "99",
        action: "suspend",
        orgIds: ["2", "4"],
      },
      expect.any(Object),
    );

    await events.click(screen.getByTestId("super-admin-groups-remove"));
    expect(mutate).toHaveBeenLastCalledWith(
      {
        userId: "99",
        action: "remove",
        orgIds: ["2", "4"],
      },
      expect.any(Object),
    );
  });

  it("adds the user to every checked organization", async () => {
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

    await events.click(screen.getByTestId("super-admin-group-add-5"));
    await events.click(screen.getByTestId("super-admin-group-add-3"));
    await events.click(screen.getByTestId("super-admin-groups-add"));

    expect(mutate).toHaveBeenCalledWith(
      {
        userId: "99",
        action: "add",
        orgIds: ["5", "3"],
        role: "member",
      },
      expect.any(Object),
    );
  });

  it("updates only the selected organizations in the mock API", async () => {
    resetSuperAdminMockState();

    const suspend = await fetch(
      "http://localhost:3000/api/admin/users/99/groups",
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
      "http://localhost:3000/api/admin/users/99/groups",
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
      "http://localhost:3000/api/admin/users/99/groups",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "remove", org_ids: ["2"] }),
      },
    );
    expect(blocked.status).toBe(409);
  });
});
