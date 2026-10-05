import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OptionService from "#/api/option-service/option-service.api";
import { superAdminService } from "#/api/super-admin-service/super-admin-service.api";
import {
  SuperAdminRowMenu,
  SuperAdminTable,
  SuperAdminUserMemberships,
} from "#/components/features/super-admin/super-admin-chrome";
import { SUPER_ADMIN_USERS } from "#/components/features/super-admin/super-admin-mock";
import { SuperAdminUsers } from "#/components/features/super-admin/super-admin-pages";
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";

async function renderUsersPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  // The Super Admin layout renders its pages only once the config has loaded.
  await queryClient.prefetchQuery({
    queryKey: QUERY_KEYS.WEB_CLIENT_CONFIG,
    queryFn: OptionService.getConfig,
  });
  const RouterStub = createRoutesStub([
    { path: "/super-admin/users", Component: SuperAdminUsers },
  ]);
  render(
    <QueryClientProvider client={queryClient}>
      <RouterStub initialEntries={["/super-admin/users"]} />
    </QueryClientProvider>,
  );
  await screen.findByTestId("super-admin-users");
}

describe("Super Admin Users page", () => {
  beforeEach(() => {
    vi.spyOn(superAdminService, "listUsers").mockResolvedValue([]);
    vi.spyOn(superAdminService, "listOrganizations").mockResolvedValue([]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("offers Provision User when the server has user provisioning enabled", async () => {
    // Arrange
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({ user_provisioning_enabled: true }),
    );

    // Act
    await renderUsersPage();

    // Assert
    expect(
      screen.getByRole("button", { name: "SUPER_ADMIN$PROVISION_USER" }),
    ).toBeInTheDocument();
    expect(screen.getByText("SUPER_ADMIN$USERS_SUBLINE")).toBeInTheDocument();
  });

  it("hides Provision User when the server has user provisioning disabled", async () => {
    // Arrange
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({ user_provisioning_enabled: false }),
    );

    // Act
    await renderUsersPage();

    // Assert
    expect(
      screen.queryByRole("button", { name: "SUPER_ADMIN$PROVISION_USER" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("SUPER_ADMIN$USERS_SUBLINE_NO_PROVISION"),
    ).toBeInTheDocument();
  });

  it("does not link another user's personal workspace", async () => {
    // Arrange
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig(),
    );
    vi.spyOn(superAdminService, "listUsers").mockResolvedValue([
      {
        user_id: "7",
        email: "sam@beta.llc",
        name: "sam",
        status: "active",
        memberships: [
          {
            org_id: "3",
            org_name: "Beta LLC",
            role: "admin",
            status: "active",
          },
          {
            org_id: "7",
            org_name: "user_7_org",
            role: "owner",
            status: "active",
          },
        ],
      },
    ]);

    // Act
    await renderUsersPage();

    // Assert
    await screen.findByText("user_7_org");
    expect(
      screen.queryByTestId("super-admin-user-org-7"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("super-admin-user-org-3")).toBeInTheDocument();
  });

  it("shows the account status, not the status of the user's memberships", async () => {
    // Arrange
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig(),
    );
    vi.spyOn(superAdminService, "listUsers").mockResolvedValue([
      {
        user_id: "7",
        email: "sam@beta.llc",
        name: "sam",
        status: "active",
        memberships: [
          {
            org_id: "3",
            org_name: "Beta LLC",
            role: "member",
            status: "inactive",
          },
        ],
      },
    ]);

    // Act
    await renderUsersPage();

    // Assert
    expect(await screen.findByText("active")).toBeInTheDocument();
    expect(screen.queryByText("inactive")).not.toBeInTheDocument();
  });
});

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
