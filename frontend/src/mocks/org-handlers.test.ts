import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { getMockCurrentUser, resetMockCurrentUser } from "./mock-current-user";

interface InvitationResponse {
  successful: { invite_url: string }[];
  email_delivery_configured: boolean;
}

describe("mock organization invitations", () => {
  beforeEach(() => resetMockCurrentUser());
  afterEach(() => resetMockCurrentUser());

  it("opens an invited member identity with member-only organization access", async () => {
    const inviteResponse = await fetch(
      "http://localhost:3000/api/organizations/2/members/invite",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          emails: ["new.member@acme.org"],
          role: "member",
        }),
      },
    );
    expect(inviteResponse.status).toBe(201);
    const invitation = (await inviteResponse.json()) as InvitationResponse;
    expect(invitation.email_delivery_configured).toBe(false);

    const inviteUrl = new URL(invitation.successful[0].invite_url);
    expect(inviteUrl.pathname).toBe("/settings/org-defaults");
    const token = inviteUrl.searchParams.get("invitation_token");
    expect(token).toBeTruthy();

    const acceptResponse = await fetch(
      "http://localhost:3000/api/organizations/members/invite/accept",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      },
    );
    expect(acceptResponse.status).toBe(200);
    expect(getMockCurrentUser().email).toBe("new.member@acme.org");

    const membershipResponse = await fetch(
      "http://localhost:3000/api/organizations/2/me",
    );
    expect(await membershipResponse.json()).toMatchObject({
      email: "new.member@acme.org",
      role: "member",
      permissions: [],
      status: "active",
    });

    const organizationsResponse = await fetch(
      "http://localhost:3000/api/organizations",
    );
    const organizations = (await organizationsResponse.json()) as {
      items: { id: string }[];
    };
    expect(organizations.items.map((organization) => organization.id)).toEqual([
      "2",
    ]);

    const superAdminResponse = await fetch(
      "http://localhost:3000/api/admin/super-admins/me",
    );
    expect(await superAdminResponse.json()).toEqual({ is_super_admin: false });
  });
});

describe("mock organization configuration policy", () => {
  it("persists a locked LLM policy without a server error", async () => {
    const updateResponse = await fetch(
      "http://localhost:3000/api/organizations/2/settings",
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          configuration_policy: { llm: { operation: "locked" } },
        }),
      },
    );

    expect(updateResponse.status).toBe(200);
    expect(await updateResponse.json()).toMatchObject({
      configuration_policy: { llm: { operation: "locked" } },
    });

    const settingsResponse = await fetch(
      "http://localhost:3000/api/organizations/2/settings",
    );
    expect(settingsResponse.status).toBe(200);
    expect(await settingsResponse.json()).toMatchObject({
      configuration_policy: { llm: { operation: "locked" } },
    });
  });
});
