import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { Budgets } from "#/components/features/budgets/budgets";
import { organizationService } from "#/api/organization-service/organization-service.api";

vi.mock("#/api/organization-service/organization-service.api", () => ({
  organizationService: {
    getBudgetSettings: vi.fn(),
    updateBudgetSettings: vi.fn(),
    upsertBudgetOverride: vi.fn(),
    deleteBudgetOverride: vi.fn(),
  },
}));

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => ({
    data: {
      slack_enabled: true,
      email_enabled: true,
    },
  }),
}));

vi.mock("#/context/use-selected-organization", () => ({
  useSelectedOrganizationId: () => ({
    organizationId: "org-123",
    setOrganizationId: vi.fn(),
  }),
}));

vi.mock("#/hooks/use-debounce", () => ({
  useDebounce: (value: string) => value,
}));

const budgetResponse = {
  enabled: true,
  monthly_limit: 1000,
  litellm_last_sync_at: "2024-01-15T12:00:00Z",
  litellm_last_sync_status: "success",
  litellm_last_sync_error: null,
  reset_day: 1,
  slack_channel: "alerts",
  slack_team_id: "T123",
  default_user_monthly_limit: 250,
  cycle_start_at: "2024-01-01T00:00:00Z",
  cycle_end_at: "2024-01-31T00:00:00Z",
  spend_status: "live" as const,
  spend_observed_at: "2024-01-15T12:00:00Z",
  current_spend: 200,
  current_spend_percentage: 20,
  unmapped_spend: 12.5,
  unmapped_member_count: 1,
  thresholds: [
    {
      id: 1,
      percentage: 75,
      email_enabled: true,
      slack_enabled: false,
    },
  ],
  users: [
    {
      user_id: "user-1",
      user_email: "user@example.com",
      user_name: "User One",
      current_spend: 25,
      monthly_limit: null,
      effective_monthly_limit: 50,
      is_disabled: false,
      is_override: true,
    },
  ],
  users_total: 1,
  users_page: 1,
  users_per_page: 50,
};

const renderBudgets = async () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <Budgets />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  await screen.findByText("Organization monthly budget");
};

describe("Budgets", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue(
      budgetResponse,
    );
    vi.mocked(organizationService.updateBudgetSettings).mockResolvedValue(
      budgetResponse,
    );
    vi.mocked(organizationService.upsertBudgetOverride).mockResolvedValue(
      budgetResponse.users[0],
    );
    vi.mocked(organizationService.deleteBudgetOverride).mockResolvedValue();
  });

  it("adds and removes thresholds, then saves updated settings", async () => {
    const user = userEvent.setup();
    await renderBudgets();

    await user.click(screen.getByRole("button", { name: /\+ Add threshold/i }));

    expect(await screen.findByText("50%")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(organizationService.updateBudgetSettings).toHaveBeenCalled();
    });

    const firstSave = vi
      .mocked(organizationService.updateBudgetSettings)
      .mock.calls.at(-1)?.[0];

    expect(
      firstSave?.payload.thresholds?.map((item) => item.percentage),
    ).toEqual([50, 75]);

    await user.click(screen.getByLabelText("Delete 50% threshold"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      const lastSave = vi
        .mocked(organizationService.updateBudgetSettings)
        .mock.calls.at(-1)?.[0];
      expect(
        lastSave?.payload.thresholds?.map((item) => item.percentage),
      ).toEqual([75]);
    });
  });

  it("saves and removes user overrides", async () => {
    const user = userEvent.setup();
    await renderBudgets();

    await user.click(screen.getByRole("button", { name: "User overrides" }));

    await screen.findByText("User One");

    await user.click(
      screen.getByRole("button", { name: "Edit budget for User One" }),
    );

    const overrideInput = screen.getByRole("spinbutton");
    await user.clear(overrideInput);
    await user.type(overrideInput, "75");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(organizationService.upsertBudgetOverride).toHaveBeenCalledWith({
        orgId: "org-123",
        userId: "user-1",
        payload: {
          monthly_limit: 75,
          is_disabled: false,
        },
      });
    });

    await user.click(screen.getByLabelText("Remove override for User One"));

    await waitFor(() => {
      expect(organizationService.deleteBudgetOverride).toHaveBeenCalledWith({
        orgId: "org-123",
        userId: "user-1",
      });
    });
  });

  it("shows unavailable spend instead of rendering it as zero", async () => {
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      spend_status: "unavailable",
      spend_observed_at: null,
      current_spend: null,
      current_spend_percentage: null,
      users: [{ ...budgetResponse.users[0], current_spend: null }],
    });

    await renderBudgets();

    expect(
      screen.getByText(/Spend data is temporarily unavailable/i),
    ).toBeInTheDocument();
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("explains governed SDK usage and unmapped LiteLLM identities", async () => {
    await renderBudgets();

    expect(
      screen.getByText(/SDK requests routed through this deployment/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/1 LiteLLM identity is not mapped/i),
    ).toHaveTextContent("$12.50 of this cycle's spend");
  });

  it("shows reconciliation errors without replacing authoritative spend", async () => {
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      litellm_last_sync_status: "error",
      litellm_last_sync_error: "member cycle baseline is unavailable",
    });

    await renderBudgets();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Existing caps are preserved",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "member cycle baseline is unavailable",
    );
    expect(screen.getByText("$200.00")).toBeInTheDocument();
  });
});
