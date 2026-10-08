import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { superAdminService } from "#/api/super-admin-service/super-admin-service.api";
import { SuperAdminDashboard } from "#/components/features/super-admin/super-admin-dashboard";
import { useSuperAdminDashboardStore } from "#/stores/super-admin-dashboard-store";

const ORGS = [
  { id: "org-1", name: "OpenHands-Test" },
  { id: "org-2", name: "OpenHands-Test-2" },
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
    usage_conversation_count: 0,
    agent_runs: 0,
    estimated_spend: 0,
    daily_usage: [],
    model_usage: [],
    agent_usage: [],
  } as never);
  vi.spyOn(organizationService, "getUserUsageStats").mockResolvedValue({
    items: [],
    has_more: false,
  } as never);
  vi.spyOn(organizationService, "getConversations").mockResolvedValue({
    items: [],
    total_items: 0,
  } as never);
}

/** Each visit to the dashboard mounts it again. */
function visitDashboard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SuperAdminDashboard />
    </QueryClientProvider>,
  );
}

/** The organizations and time windows of usage requested since `from`. */
function usageRequestsSince(from: number) {
  return vi
    .mocked(organizationService.getUsageStats)
    .mock.calls.slice(from)
    .map(([{ orgId, timeWindow }]) => `${orgId} ${timeWindow}`);
}

describe("SuperAdminDashboard scope", () => {
  beforeEach(() => {
    sessionStorage.clear();
    mockUsage();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps the selected organization and time window after leaving and coming back", async () => {
    // Arrange
    const user = userEvent.setup();
    const firstVisit = visitDashboard();
    await user.click(await screen.findByTestId("super-admin-org-compare"));
    await user.click(
      await screen.findByTestId("super-admin-org-compare-org-2"),
    );
    await user.click(screen.getByRole("button", { name: "7d" }));
    firstVisit.unmount();
    const requestsBefore = vi.mocked(organizationService.getUsageStats).mock
      .calls.length;

    // Act
    visitDashboard();

    // Assert
    await waitFor(() =>
      expect(screen.getByTestId("super-admin-org-compare")).toHaveTextContent(
        "OpenHands-Test-2",
      ),
    );
    await waitFor(() =>
      expect(usageRequestsSince(requestsBefore)).toEqual(["org-2 7d"]),
    );
    expect(
      JSON.parse(sessionStorage.getItem("super-admin-dashboard")!).state,
    ).toEqual({ selectedOrgIds: ["org-2"], timeWindow: "7d" });
  });

  it("drops a saved organization that no longer exists", async () => {
    // Arrange
    useSuperAdminDashboardStore.setState({ selectedOrgIds: ["org-removed"] });

    // Act
    visitDashboard();

    // Assert
    await waitFor(() =>
      expect(useSuperAdminDashboardStore.getState().selectedOrgIds).toEqual([]),
    );
    await waitFor(() =>
      expect(usageRequestsSince(0)).toEqual(["org-1 30d", "org-2 30d"]),
    );
  });

  it("uses 30 days when the saved time window is no longer offered", async () => {
    // Arrange
    useSuperAdminDashboardStore.setState({ timeWindow: "1y" });

    // Act
    visitDashboard();

    // Assert
    await waitFor(() =>
      expect(usageRequestsSince(0)).toEqual(["org-1 30d", "org-2 30d"]),
    );
  });
});
