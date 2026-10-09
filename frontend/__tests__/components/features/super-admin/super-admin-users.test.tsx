import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { idpService } from "#/api/idp-service/idp-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { superAdminService } from "#/api/super-admin-service/super-admin-service.api";
import {
  SuperAdminRowMenu,
  SuperAdminTable,
  SuperAdminUserMemberships,
} from "#/components/features/super-admin/super-admin-chrome";
import { SuperAdminUsers } from "#/components/features/super-admin/super-admin-pages";
import type { SuperAdminUserRow } from "#/components/features/super-admin/super-admin-types";
import { QUERY_KEYS } from "#/hooks/query/query-keys";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";

const SUPER_ADMIN_USERS: SuperAdminUserRow[] = [
  {
    id: "u-1",
    name: "openhands",
    email: "me@acme.org",
    memberships: [
      { orgId: "2", orgName: "Acme Corp", role: "owner" },
      { orgId: "4", orgName: "All Hands AI", role: "admin" },
    ],
    status: "active",
  },
  {
    id: "u-2",
    name: "Alex Rivera",
    email: "alex@acme.org",
    memberships: [
      { orgId: "2", orgName: "Acme Corp", role: "admin" },
      { orgId: "3", orgName: "Beta LLC", role: "member" },
    ],
    status: "active",
  },
  {
    id: "u-3",
    name: "Jordan Lee",
    email: "jordan@all-hands.dev",
    memberships: [{ orgId: "4", orgName: "All Hands AI", role: "member" }],
    status: "active",
  },
  {
    id: "u-4",
    name: "Sam Patel",
    email: "sam@beta.llc",
    memberships: [
      { orgId: "3", orgName: "Beta LLC", role: "admin" },
      { orgId: "5", orgName: "Northwind Labs", role: "member" },
    ],
    status: "invited",
  },
];

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

  it.each([true, false])(
    "offers only Create Sign-up Link when user provisioning is %s and the local password IDP is on",
    async (provisioningEnabled) => {
      // Arrange
      vi.spyOn(OptionService, "getConfig").mockResolvedValue(
        createMockWebClientConfig({
          user_provisioning_enabled: provisioningEnabled,
          feature_flags: {
            enable_billing: false,
            hide_llm_settings: false,
            enable_jira: false,
            enable_jira_dc: false,
            enable_linear: false,
            hide_users_page: false,
            hide_billing_page: false,
            hide_integrations_page: false,
            enable_onboarding: false,
            enable_integrated_idp: true,
          },
        }),
      );

      // Act
      await renderUsersPage();

      // Assert
      expect(
        screen.getByRole("button", { name: "ORG$CREATE_SIGNUP_LINK" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "SUPER_ADMIN$PROVISION_USER" }),
      ).not.toBeInTheDocument();
      expect(screen.getByText("SUPER_ADMIN$USERS_SUBLINE")).toBeInTheDocument();
    },
  );

  it("hides the Create Sign-up Link button when the local password IDP is off", async () => {
    // Arrange: the default mock config has no `enable_integrated_idp` flag.
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig(),
    );

    // Act
    await renderUsersPage();

    // Assert: there is no password-based account for a link recipient to
    // set up without the local IDP, so the affordance must not be offered.
    await screen.findByTestId("super-admin-users");
    expect(
      screen.queryByRole("button", { name: "ORG$CREATE_SIGNUP_LINK" }),
    ).not.toBeInTheDocument();
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
    await screen.findByText("ORG$PERSONAL_WORKSPACE");
    expect(
      screen.queryByTestId("super-admin-user-org-7"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("super-admin-user-org-3")).toBeInTheDocument();
  });

  it("shows a user's personal workspace as Personal Workspace and keeps team names", async () => {
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
    expect(
      await screen.findByText("ORG$PERSONAL_WORKSPACE"),
    ).toBeInTheDocument();
    expect(screen.getByText("Beta LLC")).toBeInTheDocument();
    expect(screen.queryByText("user_7_org")).not.toBeInTheDocument();
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

  describe("Reset Password", () => {
    const SAM: SuperAdminUserRow = {
      id: "7",
      name: "sam",
      email: "sam@beta.llc",
      memberships: [
        { orgId: "3", orgName: "Beta LLC", role: "admin", status: "active" },
      ],
      status: "active",
    };

    beforeEach(() => {
      vi.spyOn(superAdminService, "listUsers").mockResolvedValue([
        {
          user_id: SAM.id,
          email: SAM.email,
          name: SAM.name,
          status: "active",
          memberships: [
            {
              org_id: "3",
              org_name: "Beta LLC",
              role: "admin",
              status: "active",
            },
          ],
        },
      ]);
    });

    it("does not show the Reset Password option when enable_integrated_idp is off", async () => {
      // Arrange
      vi.spyOn(OptionService, "getConfig").mockResolvedValue(
        createMockWebClientConfig(),
      );

      // Act
      await renderUsersPage();
      const user = userEvent.setup();
      await user.click(
        await screen.findByTestId(`super-admin-user-actions-${SAM.id}`),
      );

      // Assert
      expect(
        screen.queryByTestId(`super-admin-reset-password-${SAM.id}`),
      ).not.toBeInTheDocument();
    });

    it("lets a super admin create a password-reset link for a user when enable_integrated_idp is on", async () => {
      // Arrange
      vi.spyOn(OptionService, "getConfig").mockResolvedValue(
        createMockWebClientConfig({
          feature_flags: {
            enable_billing: false,
            hide_llm_settings: false,
            enable_jira: false,
            enable_jira_dc: false,
            enable_linear: false,
            hide_users_page: false,
            hide_billing_page: false,
            hide_integrations_page: false,
            enable_onboarding: false,
            enable_integrated_idp: true,
          },
        }),
      );
      const createSignupLinkSpy = vi
        .spyOn(idpService, "createSignupLink")
        .mockResolvedValue({
          url: "https://example.com/signup/abc123",
          role: "member",
          expires_at: new Date(Date.now() + 86_400_000).toISOString(),
        });
      const user = userEvent.setup();

      // Act
      await renderUsersPage();
      await user.click(
        await screen.findByTestId(`super-admin-user-actions-${SAM.id}`),
      );
      await user.click(
        await screen.findByTestId(`super-admin-reset-password-${SAM.id}`),
      );

      // Assert
      expect(createSignupLinkSpy).toHaveBeenCalledExactlyOnceWith({
        email: SAM.email,
        role: "member",
      });
      const resultModal = await screen.findByTestId(
        "password-reset-link-result-modal",
      );
      expect(within(resultModal).getByText(SAM.email)).toBeInTheDocument();
      // "invite" wording doesn't fit a password reset, unlike the sign-up
      // link modals that reuse this same button.
      expect(
        within(resultModal).getByText("ORG$COPY_LINK"),
      ).toBeInTheDocument();
    });
  });

  describe("Create Sign-up Link", () => {
    beforeEach(() => {
      vi.spyOn(OptionService, "getConfig").mockResolvedValue(
        createMockWebClientConfig({
          user_provisioning_enabled: false,
          feature_flags: {
            enable_billing: false,
            hide_llm_settings: false,
            enable_jira: false,
            enable_jira_dc: false,
            enable_linear: false,
            hide_users_page: false,
            hide_billing_page: false,
            hide_integrations_page: false,
            enable_onboarding: false,
            enable_integrated_idp: true,
          },
        }),
      );
    });

    it("mints an instance-wide sign-up link and shows the copyable result, instead of sending an email invite", async () => {
      // Arrange: this page has no single org in context, so the link is
      // not scoped to an org -- see `MintSignupLinkModal`'s `orgId: null`.
      const user = userEvent.setup();
      const createSignupLinkSpy = vi
        .spyOn(idpService, "createSignupLink")
        .mockResolvedValue({
          url: "https://app.example.com/oauth/idp/invite?token=abc123",
          role: "member",
          expires_at: "2026-01-08T00:00:00Z",
        });
      await renderUsersPage();

      // Act
      await user.click(
        screen.getByRole("button", { name: "ORG$CREATE_SIGNUP_LINK" }),
      );
      const modal = await screen.findByTestId("mint-signup-link-modal");
      await user.type(
        within(modal).getByTestId("signup-link-email-input"),
        "new@acme.org",
      );
      await user.click(within(modal).getByRole("button", { name: /create/i }));

      // Assert: no org-scoped email invite was ever sent -- the local
      // password IDP's sign-up link is the only mechanism this button uses.
      expect(createSignupLinkSpy).toHaveBeenCalledExactlyOnceWith({
        email: "new@acme.org",
        role: "member",
        orgId: undefined,
      });
      const resultModal = await screen.findByTestId("signup-link-result");
      expect(within(resultModal).getByText("new@acme.org")).toBeInTheDocument();
      expect(
        within(resultModal).getByTestId("copy-invite-link-button"),
      ).toBeInTheDocument();
    });

    it("offers only team organizations in the optional org picker, and scopes the link when one is chosen", async () => {
      // Arrange
      const user = userEvent.setup();
      vi.spyOn(superAdminService, "listOrganizations").mockResolvedValue([
        {
          id: "2",
          name: "Acme Corp",
          contact_email: "ops@acme.org",
          contact_name: null,
          member_count: 3,
          is_personal: false,
          status: "active",
        },
        {
          id: "7",
          name: "user_7_org",
          contact_email: "sam@beta.llc",
          contact_name: null,
          member_count: 1,
          is_personal: true,
          status: "active",
        },
      ]);
      const createSignupLinkSpy = vi
        .spyOn(idpService, "createSignupLink")
        .mockResolvedValue({
          url: "https://app.example.com/oauth/idp/invite?token=abc123",
          role: "member",
          expires_at: "2026-01-08T00:00:00Z",
        });
      await renderUsersPage();

      // Act
      await user.click(
        screen.getByRole("button", { name: "ORG$CREATE_SIGNUP_LINK" }),
      );
      const modal = await screen.findByTestId("mint-signup-link-modal");
      const orgDropdown = within(modal).getByTestId("signup-link-org-dropdown");
      await user.click(within(orgDropdown).getByTestId("dropdown-trigger"));

      // Assert: personal workspaces aren't a valid invite target.
      expect(
        await screen.findByRole("option", { name: "Acme Corp" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("option", { name: "user_7_org" }),
      ).not.toBeInTheDocument();

      // Act: pick the org and submit.
      await user.click(screen.getByRole("option", { name: "Acme Corp" }));
      await user.type(
        within(modal).getByTestId("signup-link-email-input"),
        "new@acme.org",
      );
      await user.click(within(modal).getByRole("button", { name: /create/i }));

      // Assert: the link is now scoped to the chosen org.
      expect(createSignupLinkSpy).toHaveBeenCalledExactlyOnceWith({
        email: "new@acme.org",
        role: "member",
        orgId: "2",
      });
    });
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
