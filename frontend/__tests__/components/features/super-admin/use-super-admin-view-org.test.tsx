import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSuperAdminViewOrg } from "#/components/features/super-admin/use-super-admin-view-org";

const { openOrg, state } = vi.hoisted(() => ({
  openOrg: vi.fn(),
  state: {
    meLoading: false,
    userId: "99" as string | undefined,
    usersReady: true,
    memberships: [
      { org_id: "2", org_name: "Acme Corp", role: "owner", status: "active" },
    ],
  },
}));

vi.mock("#/components/features/super-admin/use-super-admin-open-org", () => ({
  useSuperAdminOpenOrg: () => openOrg,
}));

vi.mock("#/hooks/query/use-me", () => ({
  useMe: () => ({
    isLoading: state.meLoading,
    data: state.userId ? { user_id: state.userId } : undefined,
  }),
}));

vi.mock("#/hooks/query/use-super-admin", () => ({
  useSuperAdminUsers: () => ({
    isSuccess: state.usersReady,
    data: [
      {
        user_id: "99",
        memberships: state.memberships,
      },
    ],
  }),
}));

vi.mock("#/hooks/mutation/use-super-admin-mutations", () => ({
  useUpdateSuperAdminUserGroups: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}));

function Harness({ orgId, orgName }: { orgId: string; orgName: string }) {
  const { viewOrg, pendingOrg } = useSuperAdminViewOrg();
  return (
    <>
      <button type="button" onClick={() => viewOrg(orgId, orgName)}>
        View
      </button>
      {pendingOrg ? (
        <div data-testid="super-admin-grant-self-access">{pendingOrg.orgName}</div>
      ) : null}
    </>
  );
}

describe("useSuperAdminViewOrg", () => {
  beforeEach(() => {
    openOrg.mockClear();
    state.meLoading = false;
    state.userId = "99";
    state.usersReady = true;
    state.memberships = [
      { org_id: "2", org_name: "Acme Corp", role: "owner", status: "active" },
    ];
  });

  it("opens an organization the super admin already belongs to", async () => {
    const events = userEvent.setup();
    render(<Harness orgId="2" orgName="Acme Corp" />);

    await events.click(screen.getByRole("button", { name: "View" }));

    expect(openOrg).toHaveBeenCalledWith("2", "Acme Corp");
    expect(
      screen.queryByTestId("super-admin-grant-self-access"),
    ).not.toBeInTheDocument();
  });

  it("asks for access instead of opening an organization they are not in", async () => {
    const events = userEvent.setup();
    render(<Harness orgId="3" orgName="Beta LLC" />);

    await events.click(screen.getByRole("button", { name: "View" }));

    expect(openOrg).not.toHaveBeenCalled();
    expect(screen.getByTestId("super-admin-grant-self-access")).toHaveTextContent(
      "Beta LLC",
    );
  });
});
