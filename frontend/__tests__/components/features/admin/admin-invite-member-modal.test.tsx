import { within, screen, render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { AdminInviteMemberModal } from "#/components/features/admin/admin-invite-member-modal";

const renderModal = (onClose: () => void = vi.fn()) =>
  render(
    <AdminInviteMemberModal orgId="org-123" orgName="Acme" onClose={onClose} />,
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={new QueryClient()}>
          {children}
        </QueryClientProvider>
      ),
    },
  );

describe("AdminInviteMemberModal", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("invites into the passed org with the default member role", async () => {
    const inviteSpy = vi
      .spyOn(organizationService, "inviteMembers")
      .mockResolvedValue({
        successful: [],
        failed: [],
        email_delivery_configured: true,
      });
    const onClose = vi.fn();

    renderModal(onClose);

    const modal = screen.getByTestId("admin-invite-member-modal");
    const badgeInput = within(modal).getByTestId("admin-invite-emails");
    await userEvent.type(badgeInput, "someone@acme.org ");

    // In tests the i18n mock returns the key, so the primary button label is
    // the raw key ORG$INVITE_ORG_MEMBERS.
    const inviteButton = within(modal).getByText("ORG$INVITE_ORG_MEMBERS");
    await userEvent.click(inviteButton);

    expect(inviteSpy).toHaveBeenCalledWith({
      orgId: "org-123",
      emails: ["someone@acme.org"],
      role: "member",
    });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("supports inviting with the owner role", async () => {
    const inviteSpy = vi
      .spyOn(organizationService, "inviteMembers")
      .mockResolvedValue({
        successful: [],
        failed: [],
        email_delivery_configured: true,
      });

    renderModal();

    const modal = screen.getByTestId("admin-invite-member-modal");
    const badgeInput = within(modal).getByTestId("admin-invite-emails");
    await userEvent.type(badgeInput, "founder@acme.org ");

    // Open the role dropdown and pick "owner" (label key ORG$ROLE_OWNER).
    const roleDropdown = within(modal).getByTestId(
      "admin-invite-role-dropdown",
    );
    const toggle = within(roleDropdown).getByRole("button");
    await userEvent.click(toggle);
    await userEvent.click(screen.getByText("ORG$ROLE_OWNER"));

    const inviteButton = within(modal).getByText("ORG$INVITE_ORG_MEMBERS");
    await userEvent.click(inviteButton);

    expect(inviteSpy).toHaveBeenCalledWith({
      orgId: "org-123",
      emails: ["founder@acme.org"],
      role: "owner",
    });
  });
});
