import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { superAdminService } from "#/api/super-admin-service/super-admin-service.api";
import { SuperAdminDashboard } from "#/components/features/super-admin/super-admin-dashboard";

// Two team organizations, each with one conversation by the same admin: one
// finished, one still running.
const ORGS = [
  { id: "org-1", name: "OpenHands-Test", status: "finished" },
  { id: "org-2", name: "OpenHands-Test-2", status: "running" },
];

function mockUsage() {
  vi.spyOn(superAdminService, "listOrganizations").mockResolvedValue(
    ORGS.map(({ id, name }) => ({
      id,
      name,
      contact_email: null,
      contact_name: null,
      member_count: 1,
      is_personal: false,
      status: "active",
    })),
  );
  vi.spyOn(organizationService, "getUsageStats").mockResolvedValue({
    usage_conversation_count: 1,
    agent_runs: 1,
    estimated_spend: 0,
    daily_usage: [],
    model_usage: [],
    agent_usage: [],
  } as never);
  vi.spyOn(organizationService, "getUserUsageStats").mockResolvedValue({
    items: [
      {
        user_id: "user-1",
        user_email: "admin@example.com",
        user_name: null,
        conversation_count: 1,
        first_conversation_at: null,
        last_conversation_at: null,
        first_login_at: null,
        last_login_at: null,
        spend_mtd: 0,
        spend_ytd: 0,
        spend_lifetime: 0,
        budget_monthly_limit: null,
        budget_is_disabled: false,
        prs_merged: null,
      },
    ],
    has_more: false,
  } as never);
  vi.spyOn(organizationService, "getConversations").mockImplementation(
    async ({ orgId }) => {
      const org = ORGS.find(({ id }) => id === orgId)!;
      return {
        items: [
          {
            id: `conversation-${org.id}`,
            title: `${org.status} conversation`,
            user_email: "admin@example.com",
            total_tokens: 0,
            accumulated_cost: 0,
            created_at: "2026-10-08T08:00:00",
            updated_at: "2026-10-08T08:00:00",
            pr_number: [],
            selected_repository: null,
            pr_merged: null,
            agent_kind: "openhands",
            llm_model: null,
            trigger: "gui",
            execution_status: org.status,
            sandbox_status: "RUNNING",
          },
        ],
        total_items: 1,
      } as never;
    },
  );
}

function renderDashboard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SuperAdminDashboard />
    </QueryClientProvider>,
  );
}

describe("SuperAdminDashboard", () => {
  beforeEach(() => {
    mockUsage();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists every conversation the Users tab counts, whatever its status", async () => {
    // Arrange
    const user = userEvent.setup();
    renderDashboard();
    await waitFor(() =>
      expect(organizationService.getConversations).toHaveBeenCalledTimes(2),
    );

    // Act
    await user.click(
      await screen.findByRole("button", { name: /^Conversations\s*2$/ }),
    );

    // Assert: the finished conversation is listed next to the running one.
    expect(
      screen.getByRole("cell", { name: "OpenHands-Test" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("cell", { name: "OpenHands-Test-2" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Users\s*2$/ })).toBeVisible();
  });

  it("resets the status filter to all statuses", async () => {
    // Arrange
    const user = userEvent.setup();
    renderDashboard();
    await user.click(
      await screen.findByRole("button", { name: /^Conversations\s*2$/ }),
    );
    await user.click(screen.getByTestId("conversation-filters-button"));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Status" }),
      "running",
    );
    expect(
      screen.queryByRole("cell", { name: "OpenHands-Test" }),
    ).not.toBeInTheDocument();

    // Act
    await user.click(screen.getByRole("button", { name: "Reset filters" }));

    // Assert
    expect(
      screen.getByRole("cell", { name: "OpenHands-Test" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("cell", { name: "OpenHands-Test-2" }),
    ).toBeInTheDocument();
  });

  it("counts users' conversations in the selected time window", async () => {
    // Arrange
    const user = userEvent.setup();
    renderDashboard();
    await waitFor(() =>
      expect(organizationService.getUserUsageStats).toHaveBeenCalledWith(
        expect.objectContaining({ timeWindow: "30d" }),
      ),
    );

    // Act
    await user.click(screen.getByRole("button", { name: "7d" }));

    // Assert
    await waitFor(() =>
      expect(organizationService.getUserUsageStats).toHaveBeenCalledWith(
        expect.objectContaining({ orgId: "org-2", timeWindow: "7d" }),
      ),
    );
  });
});
