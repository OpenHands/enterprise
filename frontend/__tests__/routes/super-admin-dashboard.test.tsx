import { screen, render, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import SuperAdminDashboard from "#/routes/super-admin-dashboard";
import { adminService } from "#/api/admin-service/admin-service.api";
import { organizationService } from "#/api/organization-service/organization-service.api";

// The dashboard's super-admin status query is only enabled on enterprise
// self-hosted installs (via useAppMode); force that here so the mocked
// adminService calls are exercised.
vi.mock("#/hooks/use-app-mode", () => ({
  useAppMode: () => ({
    isOss: false,
    isSaas: true,
    isCloud: false,
    isSelfHosted: true,
    isEnterpriseSelfHosted: true,
    isEnterpriseCloud: false,
    appMode: "saas",
    deploymentMode: "self_hosted",
  }),
}));

const renderDashboard = () =>
  render(<SuperAdminDashboard />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={new QueryClient()}>
        {children}
      </QueryClientProvider>
    ),
  });

describe("SuperAdminDashboard", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("lists all instance organizations for a super admin", async () => {
    vi.spyOn(adminService, "getSuperAdminStatus").mockResolvedValue({
      is_super_admin: true,
    });
    vi.spyOn(adminService, "listAllOrgs").mockResolvedValue({
      organizations: [
        {
          id: "org-1",
          name: "Acme",
          contact_email: "acme@example.com",
          is_default: false,
        },
        {
          id: "org-2",
          name: "Globex",
          contact_email: null,
          is_default: true,
        },
      ],
    });

    renderDashboard();

    expect(await screen.findByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Globex")).toBeInTheDocument();
    const rows = screen.getAllByTestId("super-admin-org-row");
    expect(rows).toHaveLength(2);
  });

  it("opens the invite modal for the chosen org", async () => {
    vi.spyOn(adminService, "getSuperAdminStatus").mockResolvedValue({
      is_super_admin: true,
    });
    vi.spyOn(adminService, "listAllOrgs").mockResolvedValue({
      organizations: [
        {
          id: "org-1",
          name: "Acme",
          contact_email: "acme@example.com",
          is_default: false,
        },
      ],
    });
    const inviteSpy = vi
      .spyOn(organizationService, "inviteMembers")
      .mockResolvedValue({
        successful: [],
        failed: [],
        email_delivery_configured: true,
      });

    renderDashboard();

    const row = await screen.findByTestId("super-admin-org-row");
    await userEvent.click(within(row).getByTestId("super-admin-invite-button"));

    const modal = await screen.findByTestId("admin-invite-member-modal");
    await userEvent.type(
      within(modal).getByTestId("admin-invite-emails"),
      "new@acme.org ",
    );
    await userEvent.click(within(modal).getByText("ORG$INVITE_ORG_MEMBERS"));

    expect(inviteSpy).toHaveBeenCalledWith({
      orgId: "org-1",
      emails: ["new@acme.org"],
      role: "member",
    });
  });

  it("shows the empty state when there are no team orgs", async () => {
    vi.spyOn(adminService, "getSuperAdminStatus").mockResolvedValue({
      is_super_admin: true,
    });
    vi.spyOn(adminService, "listAllOrgs").mockResolvedValue({
      organizations: [],
    });

    renderDashboard();

    expect(
      await screen.findByTestId("super-admin-orgs-empty"),
    ).toBeInTheDocument();
  });
});
