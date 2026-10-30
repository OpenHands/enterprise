import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminDashboardButton } from "#/components/shared/buttons/admin-dashboard-button";

const mockMe = vi.hoisted(() => ({
  data: undefined as { role: string; permissions?: string[] } | undefined,
  isLoading: false,
}));

vi.mock("#/hooks/query/use-me", () => ({ useMe: () => mockMe }));
vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => ({ data: { app_mode: "saas" } }),
}));
vi.mock("#/context/use-selected-organization", () => ({
  useSelectedOrganizationId: () => ({ organizationId: "org-1" }),
}));

const renderButton = () =>
  render(
    <MemoryRouter>
      <AdminDashboardButton />
    </MemoryRouter>,
  );

describe("AdminDashboardButton", () => {
  beforeEach(() => {
    mockMe.data = undefined;
  });

  it("renders when /me grants view_org_conversations, whatever the role", () => {
    mockMe.data = { role: "member", permissions: ["view_org_conversations"] };

    renderButton();

    expect(screen.getByLabelText("Usage & Monitoring")).toHaveAttribute(
      "href",
      "/settings/usage-monitoring",
    );
  });

  it("does not render without view_org_conversations, whatever the role", () => {
    mockMe.data = { role: "owner", permissions: ["view_billing"] };

    renderButton();

    expect(screen.queryByLabelText("Usage & Monitoring")).toBeNull();
  });
});
