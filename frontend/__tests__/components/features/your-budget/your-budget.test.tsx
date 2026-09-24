import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { YourBudget } from "#/components/features/your-budget/your-budget";
import {
  organizationService,
  OrgMyBudget,
  OrgMyUsageStats,
} from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";

// 20 days into a 30-day cycle: $350 of $500 spent, 10 days until it resets.
const NOW = new Date("2026-09-21T00:00:00Z");

const createBudget = (overrides: Partial<OrgMyBudget> = {}): OrgMyBudget => ({
  enabled: true,
  monthly_limit: 500,
  is_disabled: false,
  is_override: false,
  limit_updated_at: null,
  current_spend: 350,
  cycle_start_at: "2026-09-01T00:00:00Z",
  cycle_end_at: "2026-10-01T00:00:00Z",
  spend_status: "live",
  spend_observed_at: "2026-09-21T00:00:00Z",
  ...overrides,
});

const createUsage = (
  overrides: Partial<OrgMyUsageStats> = {},
): OrgMyUsageStats => ({
  total_spend: 0,
  previous_period_spend: 0,
  daily_spend: [],
  model_usage: [],
  recent_usage: [],
  ...overrides,
});

const renderYourBudget = (
  budget: OrgMyBudget = createBudget(),
  usage: OrgMyUsageStats = createUsage(),
) => {
  vi.spyOn(organizationService, "getMyBudget").mockResolvedValue(budget);
  const getMyUsageSpy = vi
    .spyOn(organizationService, "getMyUsage")
    .mockResolvedValue(usage);

  render(<YourBudget />, {
    wrapper: ({ children }) => (
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    ),
  });

  return { getMyUsageSpy };
};

describe("YourBudget", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    useSelectedOrganizationStore.setState({ organizationId: "org-1" });
    vi.spyOn(organizationService, "getOrganizations").mockResolvedValue({
      items: [],
      currentOrgId: null,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    useSelectedOrganizationStore.setState({ organizationId: null });
  });

  describe("budget summary", () => {
    it("should show the allocation, the spend and what remains for the cycle", async () => {
      // Arrange & Act
      renderYourBudget();

      // Assert
      expect(
        await screen.findByTestId("your-budget-allocation"),
      ).toHaveTextContent("$500.00");
      expect(screen.getByTestId("your-budget-spent")).toHaveTextContent(
        "$350.00",
      );
      expect(screen.getByTestId("your-budget-remaining")).toHaveTextContent(
        "$150.00",
      );
    });

    it.each([
      { spend: 350, status: "SETTINGS$YOUR_BUDGET_STATUS_ON_TRACK · 70%" },
      { spend: 425, status: "SETTINGS$YOUR_BUDGET_STATUS_OVER_80 · 85%" },
      { spend: 475, status: "SETTINGS$YOUR_BUDGET_STATUS_OVER_90 · 95%" },
      { spend: 520, status: "SETTINGS$YOUR_BUDGET_STATUS_OVER_CAP · 104%" },
    ])(
      "should report '$status' after spending $$spend of $500",
      async ({ spend, status }) => {
        // Arrange & Act
        renderYourBudget(createBudget({ current_spend: spend }));

        // Assert
        expect(
          await screen.findByTestId("your-budget-status"),
        ).toHaveTextContent(status);
      },
    );

    it.each([
      {
        case: "the organization default",
        budget: {},
        note: "SETTINGS$YOUR_BUDGET_ORG_DEFAULT",
      },
      {
        case: "a limit the admin set for this user",
        budget: { is_override: true, limit_updated_at: "2026-08-15T00:00:00Z" },
        note: "SETTINGS$YOUR_BUDGET_SET_BY_ADMIN_ON",
      },
      {
        case: "an exemption granted by the admin",
        budget: { is_disabled: true, monthly_limit: null },
        note: "SETTINGS$YOUR_BUDGET_EXEMPT",
      },
      {
        case: "no limit at all",
        budget: { monthly_limit: null },
        note: "SETTINGS$YOUR_BUDGET_NO_LIMIT_SET",
      },
    ])(
      "should explain that the allocation is $case",
      async ({ budget, note }) => {
        // Arrange & Act
        renderYourBudget(createBudget(budget));

        // Assert
        expect(
          await screen.findByTestId("your-budget-allocation"),
        ).toHaveTextContent(note);
      },
    );

    it("should show no amount and no progress when no limit applies", async () => {
      // Arrange & Act
      renderYourBudget(createBudget({ monthly_limit: null }));

      // Assert
      expect(
        await screen.findByTestId("your-budget-allocation"),
      ).toHaveTextContent("SETTINGS$YOUR_BUDGET_NO_LIMIT");
      expect(screen.queryByTestId("your-budget-meter")).not.toBeInTheDocument();
    });

    it.each([
      { spend: 400, projection: "SETTINGS$YOUR_BUDGET_DAYS_AT_RATE" },
      { spend: 100, projection: "SETTINGS$YOUR_BUDGET_ENOUGH_FOR_CYCLE" },
    ])(
      "should project '$projection' at a pace of $$spend in 20 days",
      async ({ spend, projection }) => {
        // Arrange & Act
        renderYourBudget(createBudget({ current_spend: spend }));

        // Assert
        expect(
          await screen.findByTestId("your-budget-remaining"),
        ).toHaveTextContent(projection);
      },
    );

    it("should say spend is unavailable rather than showing zero", async () => {
      // Arrange & Act
      renderYourBudget(
        createBudget({ current_spend: null, spend_status: "unavailable" }),
      );

      // Assert
      const spent = await screen.findByTestId("your-budget-spent");
      expect(spent).toHaveTextContent("—");
      expect(spent).toHaveTextContent("SETTINGS$YOUR_BUDGET_SPEND_UNAVAILABLE");
    });

    it("should say when the spend figure is not live", async () => {
      // Arrange & Act
      renderYourBudget(createBudget({ spend_status: "stale" }));

      // Assert
      expect(await screen.findByTestId("your-budget-spent")).toHaveTextContent(
        "SETTINGS$YOUR_BUDGET_SPEND_AS_OF",
      );
    });
  });

  describe("when there is no budget to show", () => {
    it("should say budgets are not enabled and skip loading usage", async () => {
      // Arrange & Act
      const { getMyUsageSpy } = renderYourBudget(
        createBudget({ enabled: false }),
      );

      // Assert
      expect(
        await screen.findByTestId("your-budget-not-enabled"),
      ).toBeInTheDocument();
      expect(getMyUsageSpy).not.toHaveBeenCalled();
    });

    it("should show an error when the budget cannot be loaded", async () => {
      // Arrange
      vi.spyOn(organizationService, "getMyBudget").mockRejectedValue(
        new Error("unavailable"),
      );

      // Act
      render(<YourBudget />, {
        wrapper: ({ children }) => (
          <QueryClientProvider client={new QueryClient()}>
            <MemoryRouter>{children}</MemoryRouter>
          </QueryClientProvider>
        ),
      });

      // Assert
      expect(
        await screen.findByTestId("your-budget-error"),
      ).toBeInTheDocument();
    });
  });

  describe("usage breakdown", () => {
    it("should load the last 30 days by default", async () => {
      // Arrange & Act
      const { getMyUsageSpy } = renderYourBudget();

      // Assert
      await waitFor(() => {
        expect(getMyUsageSpy).toHaveBeenCalledWith({
          orgId: "org-1",
          timeWindow: "30d",
        });
      });
    });

    it.each([
      { period: "SETTINGS$YOUR_BUDGET_WEEK", timeWindow: "7d" },
      { period: "SETTINGS$YOUR_BUDGET_YEAR", timeWindow: "ytd" },
    ])(
      "should load the $timeWindow window when $period is selected",
      async ({ period, timeWindow }) => {
        // Arrange
        const { getMyUsageSpy } = renderYourBudget();
        const selector = await screen.findByTestId(
          "your-budget-period-selector",
        );

        // Act
        await userEvent.click(
          within(selector).getByRole("button", { name: period }),
        );

        // Assert
        await waitFor(() => {
          expect(getMyUsageSpy).toHaveBeenLastCalledWith({
            orgId: "org-1",
            timeWindow,
          });
        });
      },
    );

    it("should chart the spend of each day in the period", async () => {
      // Arrange & Act
      renderYourBudget(
        createBudget(),
        createUsage({
          total_spend: 5,
          daily_spend: [
            { date: "2026-09-19", cost: 3 },
            { date: "2026-09-20", cost: 2 },
          ],
        }),
      );

      // Assert
      const chart = await screen.findByTestId("your-budget-daily-chart");
      expect(within(chart).getByTitle("Sep 19: $3.00")).toBeInTheDocument();
      expect(within(chart).getByTitle("Sep 20: $2.00")).toBeInTheDocument();
    });

    it.each([
      { spend: 88, trend: "SETTINGS$YOUR_BUDGET_TREND_LESS" },
      { spend: 120, trend: "SETTINGS$YOUR_BUDGET_TREND_MORE" },
    ])(
      "should report '$trend' for $$spend against $100 in the previous period",
      async ({ spend, trend }) => {
        // Arrange & Act
        renderYourBudget(
          createBudget(),
          createUsage({ total_spend: spend, previous_period_spend: 100 }),
        );

        // Assert
        expect(
          await screen.findByTestId("your-budget-trend"),
        ).toHaveTextContent(trend);
      },
    );

    it("should not compare against a previous period without spend", async () => {
      // Arrange & Act
      renderYourBudget(createBudget(), createUsage({ total_spend: 88 }));

      // Assert
      expect(
        await screen.findByTestId("your-budget-period-total"),
      ).toHaveTextContent("$88.00");
      expect(screen.queryByTestId("your-budget-trend")).not.toBeInTheDocument();
    });

    it("should show what each model cost", async () => {
      // Arrange & Act
      renderYourBudget(
        createBudget(),
        createUsage({
          model_usage: [
            {
              model_name: "claude-sonnet-4-5",
              conversation_count: 1,
              total_tokens: 0,
              total_cost: 142.5,
            },
          ],
        }),
      );

      // Assert
      const models = await screen.findByTestId("your-budget-models");
      expect(models).toHaveTextContent("claude-sonnet-4-5");
      expect(models).toHaveTextContent("$142.50");
    });

    it("should link each recent conversation to its page with its cost", async () => {
      // Arrange & Act
      renderYourBudget(
        createBudget(),
        createUsage({
          recent_usage: [
            {
              conversation_id: "conv-1",
              title: "Fix login",
              updated_at: "2026-09-20T23:00:00Z",
              accumulated_cost: 2.34,
            },
          ],
        }),
      );

      // Assert
      const link = await screen.findByRole("link", { name: /Fix login/ });
      expect(link).toHaveAttribute("href", "/canvas/conversations/conv-1");
      expect(link).toHaveTextContent("$2.34");
    });

    it("should name a conversation that has no title yet", async () => {
      // Arrange & Act
      renderYourBudget(
        createBudget(),
        createUsage({
          recent_usage: [
            {
              conversation_id: "conv-1",
              title: null,
              updated_at: null,
              accumulated_cost: 0,
            },
          ],
        }),
      );

      // Assert
      expect(await screen.findByTestId("your-budget-recent")).toHaveTextContent(
        "SETTINGS$YOUR_BUDGET_UNTITLED",
      );
    });

    it("should say there is no usage instead of drawing an empty chart", async () => {
      // Arrange & Act
      renderYourBudget(createBudget(), createUsage());

      // Assert
      expect(
        (await screen.findAllByText("SETTINGS$YOUR_BUDGET_NO_USAGE")).length,
      ).toBeGreaterThan(0);
      expect(
        screen.queryByTestId("your-budget-daily-chart"),
      ).not.toBeInTheDocument();
    });
  });
});
