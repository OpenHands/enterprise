import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { AxiosError } from "axios";
import { Budgets } from "#/components/features/budgets/budgets";
import { organizationService } from "#/api/organization-service/organization-service.api";
import * as ToastHandlers from "#/utils/custom-toast-handlers";

vi.mock("#/api/organization-service/organization-service.api", () => ({
  organizationService: {
    getBudgetSettings: vi.fn(),
    updateBudgetSettings: vi.fn(),
    upsertBudgetOverride: vi.fn(),
    deleteBudgetOverride: vi.fn(),
  },
}));

vi.mock("react-i18next", async () => {
  const actual =
    await vi.importActual<typeof import("react-i18next")>("react-i18next");
  return {
    ...actual,
    useTranslation: () => ({
      t: (key: string, params?: Record<string, string>) => {
        const translations: Record<string, string> = {
          SETTINGS$BUDGETS_NEXT_RESET: `Next reset: ${params?.date} at 00:00 UTC.`,
          SETTINGS$BUDGETS_RESET_DAY_CHANGE_HELPER:
            "Saving keeps current organization and individual spending.",
          SETTINGS$BUDGETS_TEAM_CAP_HELPER_WITH_PRIOR_SPEND: `The ${params?.cap} team cap includes the ${params?.priorSpend} already recorded before this cycle started.`,
          SETTINGS$BUDGETS_TEAM_CAP_HELPER:
            "The team cap includes any spend already recorded before this cycle started.",
          SETTINGS$BUDGETS_THRESHOLD_PERCENTAGE_LABEL: `Threshold ${params?.position} percentage`,
          SETTINGS$BUDGETS_THRESHOLD_RANGE_ERROR:
            "Enter a percentage from 1 to 100.",
          SETTINGS$BUDGETS_THRESHOLD_DUPLICATE_ERROR:
            "Another threshold already uses this percentage.",
        };
        return translations[key] || key;
      },
      i18n: { language: "en", exists: () => false },
    }),
  };
});

const mockUseConfig = vi.fn(() => ({
  data: {
    slack_enabled: true,
    email_enabled: true,
    feature_flags: { enable_litellm: true },
  },
}));

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => mockUseConfig(),
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
  email_alerts_available: true,
  slack_integration_configured: true,
  slack_workspace_connected: true,
  enabled: true,
  monthly_limit: 1000,
  litellm_last_sync_at: "2024-01-15T12:00:00Z",
  litellm_last_sync_status: "success",
  litellm_last_sync_error: null,
  reconciliation_state: "healthy" as const,
  reconciliation_error: null,
  desired_team_max_budget: 1000,
  applied_team_max_budget: 1000,
  budget_policy_matches: true,
  applied_at: "2024-01-15T12:00:00Z",
  applied_policy_observed_at: "2024-01-15T12:00:00Z",
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
    mockUseConfig.mockReturnValue({
      data: {
        slack_enabled: true,
        email_enabled: true,
        feature_flags: { enable_litellm: true },
      },
    });
  });

  it("shows a 'please enable LiteLLM' placeholder and skips the fetch when the feature flag is off", async () => {
    mockUseConfig.mockReturnValue({
      data: {
        slack_enabled: true,
        email_enabled: true,
        feature_flags: { enable_litellm: false },
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <Budgets />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await screen.findByText("Please enable LiteLLM to use this feature.");
    expect(organizationService.getBudgetSettings).not.toHaveBeenCalled();
  });

  it("keeps a rejected settings edit through refetch and retries the draft", async () => {
    const user = userEvent.setup();
    const error = new AxiosError("503");
    error.response = { data: { detail: {
      code: "budget_change_rejected",
      message: "Budget change wasn't saved. Your previous limits remain in effect. Please retry.",
      previous_policy_verified: true,
    } } } as AxiosError["response"];
    vi.mocked(organizationService.updateBudgetSettings).mockRejectedValueOnce(error);
    vi.mocked(organizationService.getBudgetSettings)
      .mockResolvedValueOnce(budgetResponse)
      .mockResolvedValue({ ...budgetResponse, current_spend: 201 });
    await renderBudgets();
    const input = screen.getByLabelText("Monthly limit");
    await user.clear(input);
    await user.type(input, "500");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText(/Budget change wasn't saved/);
    await waitFor(() => expect(organizationService.getBudgetSettings).toHaveBeenCalledTimes(2));
    expect(input).toHaveValue("500");
    expect(screen.getByText(/of \$1,000 spent/)).toBeInTheDocument();
    expect(screen.queryByText(/of \$500 spent/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(organizationService.updateBudgetSettings).toHaveBeenCalledTimes(2));
    expect(organizationService.updateBudgetSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({payload: expect.objectContaining({monthly_limit: 500})}),
    );
    await waitFor(() => expect(screen.queryByText(/Budget change wasn't saved/)).not.toBeInTheDocument());
  });

  it("keeps the individual editor open after a rejected save", async () => {
    const user = userEvent.setup();
    const error = new AxiosError("503");
    error.response = { data: { detail: {
      code: "budget_change_rejected", message: "Budget change wasn't saved. Please retry.",
    } } } as AxiosError["response"];
    vi.mocked(organizationService.upsertBudgetOverride).mockRejectedValueOnce(error);
    await renderBudgets();
    await user.click(screen.getByRole("button", { name: "User overrides" }));
    await user.click(screen.getByLabelText("Edit budget for User One"));
    const input = screen.getByLabelText("Monthly budget for User One");
    await user.clear(input);
    await user.type(input, "75");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/Budget change wasn't saved/);
    expect(input).toHaveValue("75");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByLabelText("Monthly budget for User One")).not.toBeInTheDocument());
  });

  it("clears a rejected individual edit when Cancel discards the draft", async () => {
    const user = userEvent.setup();
    const error = new AxiosError("503");
    error.response = { data: { detail: {
      code: "budget_change_rejected", message: "Budget change wasn't saved. Please retry.",
    } } } as AxiosError["response"];
    vi.mocked(organizationService.upsertBudgetOverride).mockRejectedValueOnce(error);
    await renderBudgets();
    await user.click(screen.getByRole("button", { name: "User overrides" }));
    await user.click(screen.getByLabelText("Edit budget for User One"));
    await user.clear(screen.getByLabelText("Monthly budget for User One"));
    await user.type(screen.getByLabelText("Monthly budget for User One"), "75");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/Budget change wasn't saved/);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/Budget change wasn't saved/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Monthly budget for User One")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("Edit budget for User One"));
    expect(screen.getByLabelText("Monthly budget for User One")).toHaveValue("50");
  });

  it("previews the selected reset date and preserves the saved date on reload", async () => {
    const user = userEvent.setup();
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      reset_day: 15,
      cycle_end_at: "2099-10-15T00:00:00Z",
    });
    await renderBudgets();
    expect(
      screen.getByText("Next reset: October 15, 2099 at 00:00 UTC."),
    ).toBeInTheDocument();
    await user.click(screen.getByTestId("org-billing-cycle"));
    await user.click(screen.getByRole("option", { name: "1st of each month" }));
    expect(
      screen.getByText(
        "Saving keeps current organization and individual spending.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Next reset: October 15, 2099 at 00:00 UTC."),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(organizationService.updateBudgetSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        payload: expect.objectContaining({ reset_day: 1 }),
      }),
    );
  });

  it.each([
    [false, false],
    [true, false],
    [false, true],
    [true, true],
  ])(
    "shows available alert channels (email=%s, Slack=%s)",
    async (email, slack) => {
      vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
        ...budgetResponse,
        email_alerts_available: email,
        slack_integration_configured: slack,
        slack_workspace_connected: slack,
      });
      await renderBudgets();
      expect(Boolean(screen.queryByText("Alert thresholds"))).toBe(
        email || slack,
      );
      expect(
        Boolean(screen.queryByRole("button", { name: "Email org admins" })),
      ).toBe(email);
      expect(
        Boolean(screen.queryByRole("button", { name: "# Post to Slack" })),
      ).toBe(slack);
      expect(Boolean(screen.queryByLabelText("Slack channel"))).toBe(slack);
    },
  );

  it("requires connecting Slack before enabling alerts", async () => {
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      email_alerts_available: false,
      slack_workspace_connected: false,
    });
    await renderBudgets();
    expect(
      screen.getByRole("button", { name: "# Post to Slack" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /Add threshold/ }),
    ).toBeDisabled();
    expect(screen.getByLabelText("Delete 75% threshold")).toBeDisabled();
    expect(screen.getByRole("link", { name: /Connect Slack/ })).toHaveAttribute(
      "href",
      "/settings/integrations",
    );
  });

  it("preserves hidden alert settings when saving the monthly limit", async () => {
    const user = userEvent.setup();
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      email_alerts_available: false,
      slack_integration_configured: false,
      slack_workspace_connected: false,
    });
    await renderBudgets();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(organizationService.updateBudgetSettings).toHaveBeenCalled(),
    );
    const [{ payload }] = vi.mocked(organizationService.updateBudgetSettings)
      .mock.calls[0];
    expect(payload).not.toHaveProperty("thresholds");
    expect(payload).not.toHaveProperty("slack_channel");
  });

  it.each(["email", "slack"])(
    "preserves stored %s flags while that channel is unavailable",
    async (unavailable) => {
      const user = userEvent.setup();
      vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
        ...budgetResponse,
        email_alerts_available: unavailable !== "email",
        slack_workspace_connected: unavailable !== "slack",
        thresholds: [
          { id: 1, percentage: 75, email_enabled: true, slack_enabled: true },
        ],
      });
      await renderBudgets();
      await user.click(screen.getByRole("button", { name: "Save changes" }));
      await waitFor(() =>
        expect(organizationService.updateBudgetSettings).toHaveBeenCalled(),
      );
      const [{ payload }] = vi.mocked(organizationService.updateBudgetSettings)
        .mock.calls[0];
      expect(payload.thresholds).toEqual([
        { percentage: 75, email_enabled: true, slack_enabled: true },
      ]);
    },
  );

  it("defaults a new threshold to Slack when only Slack is available", async () => {
    const user = userEvent.setup();
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      email_alerts_available: false,
    });
    await renderBudgets();
    await user.click(screen.getByRole("button", { name: /Add threshold/ }));
    await user.type(screen.getByLabelText("Threshold 2 percentage"), "50");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(organizationService.updateBudgetSettings).toHaveBeenCalled(),
    );
    expect(
      vi.mocked(organizationService.updateBudgetSettings).mock.calls[0][0]
        .payload.thresholds,
    ).toContainEqual({
      percentage: 50,
      email_enabled: false,
      slack_enabled: true,
    });
  });

  it("adds and removes thresholds, then saves updated settings", async () => {
    const user = userEvent.setup();
    // After the first save the server returns the stored rows, sorted.
    vi.mocked(organizationService.getBudgetSettings)
      .mockResolvedValueOnce(budgetResponse)
      .mockResolvedValue({
        ...budgetResponse,
        thresholds: [
          { id: 2, percentage: 50, email_enabled: true, slack_enabled: false },
          ...budgetResponse.thresholds,
        ],
      });
    await renderBudgets();

    await user.click(screen.getByRole("button", { name: /\+ Add threshold/i }));
    await user.type(screen.getByLabelText("Threshold 2 percentage"), "50");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(organizationService.updateBudgetSettings).toHaveBeenCalled();
    });

    const firstSave = vi
      .mocked(organizationService.updateBudgetSettings)
      .mock.calls.at(-1)?.[0];

    expect(
      firstSave?.payload.thresholds?.map((item) => item.percentage),
    ).toEqual([75, 50]);

    expect(await screen.findByLabelText("Threshold 1 percentage")).toHaveValue(
      "50",
    );
    expect(screen.getByLabelText("Threshold 2 percentage")).toHaveValue("75");

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

  it("focuses the new threshold and blocks saving until it has a percentage", async () => {
    const user = userEvent.setup();
    await renderBudgets();

    await user.click(screen.getByRole("button", { name: /Add threshold/ }));

    const newThreshold = screen.getByLabelText("Threshold 2 percentage");
    expect(newThreshold).toHaveFocus();
    expect(newThreshold).toHaveValue("");
    expect(
      screen.getByText("Enter a percentage from 1 to 100."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    // Repeated clicks cannot pile up blank rows.
    expect(
      screen.getByRole("button", { name: /Add threshold/ }),
    ).toBeDisabled();
    expect(screen.getAllByLabelText(/^Threshold \d+ percentage$/)).toHaveLength(
      2,
    );
  });

  it.each([
    ["0", false],
    ["1", true],
    ["100", true],
    ["101", false],
  ])(
    "accepts a threshold of %s%% only when it is within 1-100 (%s)",
    async (value, valid) => {
      const user = userEvent.setup();
      await renderBudgets();
      await user.click(screen.getByRole("button", { name: /Add threshold/ }));

      await user.type(screen.getByLabelText("Threshold 2 percentage"), value);

      expect(
        Boolean(screen.queryByText("Enter a percentage from 1 to 100.")),
      ).toBe(!valid);
      expect(
        screen
          .getByRole("button", { name: "Save changes" })
          .hasAttribute("disabled"),
      ).toBe(!valid);
    },
  );

  it("flags a percentage that another threshold already uses", async () => {
    const user = userEvent.setup();
    await renderBudgets();
    await user.click(screen.getByRole("button", { name: /Add threshold/ }));

    await user.type(screen.getByLabelText("Threshold 2 percentage"), "75");

    expect(
      screen.getAllByText("Another threshold already uses this percentage."),
    ).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("keeps only digits in the threshold percentage input", async () => {
    const user = userEvent.setup();
    await renderBudgets();
    await user.click(screen.getByRole("button", { name: /Add threshold/ }));

    await user.type(screen.getByLabelText("Threshold 2 percentage"), "a5.b0");

    expect(screen.getByLabelText("Threshold 2 percentage")).toHaveValue("50");
  });

  it("saves and removes user overrides", async () => {
    const user = userEvent.setup();
    await renderBudgets();

    await user.click(screen.getByRole("button", { name: "User overrides" }));

    await screen.findByText("User One");

    await user.click(
      screen.getByRole("button", { name: "Edit budget for User One" }),
    );

    const overrideInput = screen.getByLabelText("Monthly budget for User One");
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

  it("shows the email local part as the name when the user has no Git name", async () => {
    const user = userEvent.setup();
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      users: [{ ...budgetResponse.users[0], user_name: null }],
    });
    await renderBudgets();

    await user.click(screen.getByRole("button", { name: "User overrides" }));

    const row = (await screen.findByText("user")).closest("tr") as HTMLElement;
    expect(within(row).getAllByText("user@example.com")).toHaveLength(1);
  });

  it("presents an exempted user as Unlimited rather than Disabled", async () => {
    const user = userEvent.setup();
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      users: [
        {
          ...budgetResponse.users[0],
          monthly_limit: null,
          effective_monthly_limit: null,
          is_disabled: true,
          is_override: true,
        },
      ],
    });
    await renderBudgets();

    await user.click(screen.getByRole("button", { name: "User overrides" }));
    await screen.findByText("User One");

    // Budget cell and status pill
    const table = screen.getByRole("table");
    expect(
      within(table).getAllByText("SETTINGS$BUDGETS_UNLIMITED"),
    ).toHaveLength(2);
    expect(within(table).queryByText("Disabled")).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Edit budget for User One" }),
    );

    expect(
      screen.getByRole("checkbox", {
        name: "SETTINGS$BUDGETS_UNLIMITED_FOR_USER",
      }),
    ).toBeChecked();
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
      screen.getByRole("button", { name: "Degraded" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      /Spend data is temporarily unavailable/i,
    );
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("explains governed SDK usage and unmapped LiteLLM identities", async () => {
    const user = userEvent.setup();
    await renderBudgets();

    await user.hover(screen.getByRole("button", { name: "Healthy" }));

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /SDK requests routed through this deployment/i,
    );
    expect(
      screen.getByText(/1 LiteLLM identity is not mapped/i),
    ).toHaveTextContent("$12.50 of this cycle's spend");
  });

  it("shows reconciliation errors without replacing authoritative spend", async () => {
    const user = userEvent.setup();
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      litellm_last_sync_status: "error",
      litellm_last_sync_error: "member cycle baseline is unavailable",
      reconciliation_state: "degraded",
      reconciliation_error: "member cycle baseline is unavailable",
    });

    await renderBudgets();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "member cycle baseline is unavailable",
    );
    await user.hover(screen.getByRole("button", { name: "Degraded" }));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Degraded — The last budget update could not be completed or verified.",
    );
    expect(screen.getByText("$200.00")).toBeInTheDocument();
  });

  it("explains a team cap that includes spend recorded before the cycle started", async () => {
    const user = userEvent.setup();
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      desired_team_max_budget: 1770,
      applied_team_max_budget: 1770,
    });

    await renderBudgets();
    await user.hover(screen.getByRole("button", { name: "Healthy" }));

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "The $1,770 team cap includes the $770.00 already recorded before this cycle started.",
    );
  });

  it("explains the team cap without an amount when it equals the monthly limit", async () => {
    const user = userEvent.setup();
    await renderBudgets();

    await user.hover(screen.getByRole("button", { name: "Healthy" }));

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent(
      "The team cap includes any spend already recorded before this cycle started.",
    );
    expect(tooltip).not.toHaveTextContent(/team cap includes the \$/);
  });

  it("omits the team cap explanation when no organization budget is enforced", async () => {
    const user = userEvent.setup();
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      enabled: false,
      reconciliation_state: "inactive",
      desired_team_max_budget: null,
      applied_team_max_budget: null,
    });

    await renderBudgets();
    await user.hover(screen.getByRole("button", { name: "Inactive" }));

    expect(await screen.findByRole("tooltip")).not.toHaveTextContent(
      /already recorded before this cycle started/,
    );
  });

  it("moves the LiteLLM explanation behind a keyboard-reachable status badge", async () => {
    const user = userEvent.setup();
    await renderBudgets();

    expect(screen.queryByText(/LiteLLM has verified/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Spend reported by LiteLLM/),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    screen.getByRole("button", { name: "User overrides" }).focus();
    await user.tab();

    const badge = screen.getByRole("button", { name: "Healthy" });
    expect(badge).toHaveFocus();
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent(
      "Healthy — LiteLLM has verified the desired budget policy. Desired team cap: $1,000. Applied team cap: $1,000.",
    );
    expect(tooltip).toHaveTextContent(/Spend reported by LiteLLM from/);
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument(),
    );
  });

  it("refetches budget state after a failed settings write", async () => {
    const user = userEvent.setup();
    vi.mocked(organizationService.getBudgetSettings)
      .mockResolvedValueOnce(budgetResponse)
      .mockResolvedValueOnce({
        ...budgetResponse,
        reconciliation_state: "failed" as const,
        reconciliation_error: "verification failed",
      });
    vi.mocked(organizationService.updateBudgetSettings).mockRejectedValueOnce(
      new Error("503"),
    );

    await renderBudgets();

    await user.click(screen.getByRole("button", { name: /\+ Add threshold/i }));
    await user.type(screen.getByLabelText("Threshold 2 percentage"), "50");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(organizationService.updateBudgetSettings).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(organizationService.getBudgetSettings).toHaveBeenCalledTimes(2);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("verification failed");
  });

  it("refetches budget state after a failed member override write", async () => {
    const user = userEvent.setup();
    vi.mocked(organizationService.getBudgetSettings)
      .mockResolvedValueOnce(budgetResponse)
      .mockResolvedValueOnce({
        ...budgetResponse,
        reconciliation_state: "failed" as const,
        reconciliation_error: "override verification failed",
      });
    vi.mocked(organizationService.upsertBudgetOverride).mockRejectedValueOnce(
      new Error("503"),
    );

    await renderBudgets();

    await user.click(screen.getByRole("button", { name: "User overrides" }));
    await user.click(
      screen.getByRole("button", { name: "Edit budget for User One" }),
    );
    const overrideInput = screen.getByLabelText("Monthly budget for User One");
    await user.clear(overrideInput);
    await user.type(overrideInput, "75");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(organizationService.upsertBudgetOverride).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(organizationService.getBudgetSettings).toHaveBeenCalledTimes(2);
    });
  });

  it("refetches budget state after a failed member override delete", async () => {
    const user = userEvent.setup();
    vi.mocked(organizationService.getBudgetSettings)
      .mockResolvedValueOnce(budgetResponse)
      .mockResolvedValueOnce({
        ...budgetResponse,
        reconciliation_state: "failed" as const,
        reconciliation_error: "delete verification failed",
      });
    vi.mocked(organizationService.deleteBudgetOverride).mockRejectedValueOnce(
      new Error("503"),
    );

    await renderBudgets();

    await user.click(screen.getByRole("button", { name: "User overrides" }));
    await user.click(screen.getByLabelText("Remove override for User One"));

    await waitFor(() => {
      expect(organizationService.deleteBudgetOverride).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(organizationService.getBudgetSettings).toHaveBeenCalledTimes(2);
    });
  });

  it("does not offer a toggle to disable the organization budget", async () => {
    await renderBudgets();

    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.queryByText("Enable budget")).not.toBeInTheDocument();
  });

  it("saves the organization budget as enabled even when it is currently inactive", async () => {
    const user = userEvent.setup();
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      enabled: false,
      reconciliation_state: "inactive",
      desired_team_max_budget: null,
      applied_team_max_budget: null,
    });

    await renderBudgets();
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(organizationService.updateBudgetSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          payload: expect.objectContaining({
            enabled: true,
            monthly_limit: 1000,
          }),
        }),
      );
    });
  });

  it("disables saving the organization budget until a monthly limit is entered", async () => {
    const user = userEvent.setup();
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      enabled: false,
      monthly_limit: null,
      reconciliation_state: "inactive",
      desired_team_max_budget: null,
      applied_team_max_budget: null,
    });

    await renderBudgets();

    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();

    await user.type(screen.getByLabelText("Monthly limit"), "500");

    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  });

  it("updates the default budget with the entered amount", async () => {
    const user = userEvent.setup();
    await renderBudgets();
    await user.click(
      screen.getByRole("button", { name: "Default budget for users" }),
    );
    const input = screen.getByLabelText("Default amount");
    await user.clear(input);
    await user.type(input, "300");

    await user.click(screen.getByRole("button", { name: "Update default" }));

    await waitFor(() => {
      expect(organizationService.updateBudgetSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          payload: { default_user_monthly_limit: 300 },
        }),
      );
    });
  });

  it("keeps only digits and one decimal point in the monthly limit", async () => {
    const user = userEvent.setup();
    await renderBudgets();
    const input = screen.getByLabelText("Monthly limit");
    await user.clear(input);
    await user.type(input, "ab1e2-.5");

    expect(input).toHaveValue("12.5");

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(organizationService.updateBudgetSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          payload: expect.objectContaining({ monthly_limit: 12.5 }),
        }),
      );
    });
  });

  it("keeps only digits and one decimal point in the default amount", async () => {
    const user = userEvent.setup();
    await renderBudgets();
    await user.click(
      screen.getByRole("button", { name: "Default budget for users" }),
    );
    const input = screen.getByLabelText("Default amount");
    await user.clear(input);
    await user.type(input, "ab1e2-.5");

    expect(input).toHaveValue("12.5");

    await user.click(screen.getByRole("button", { name: "Update default" }));

    await waitFor(() => {
      expect(organizationService.updateBudgetSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          payload: { default_user_monthly_limit: 12.5 },
        }),
      );
    });
  });

  it("keeps only digits and one decimal point in a user override", async () => {
    const user = userEvent.setup();
    await renderBudgets();
    await user.click(screen.getByRole("button", { name: "User overrides" }));
    await user.click(
      screen.getByRole("button", { name: "Edit budget for User One" }),
    );
    const input = screen.getByLabelText("Monthly budget for User One");
    await user.clear(input);
    await user.type(input, "ab1e2-.5");

    expect(input).toHaveValue("12.5");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(organizationService.upsertBudgetOverride).toHaveBeenCalledWith({
        orgId: "org-123",
        userId: "user-1",
        payload: { monthly_limit: 12.5, is_disabled: false },
      });
    });
  });

  it("describes the default budget as applying to users without an override", async () => {
    const user = userEvent.setup();
    await renderBudgets();

    await user.click(
      screen.getByRole("button", { name: "Default budget for users" }),
    );

    expect(
      screen.getByRole("heading", {
        name: "SETTINGS$BUDGETS_DEFAULT_FOR_USERS",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("SETTINGS$BUDGETS_DEFAULT_FOR_USERS_DESCRIPTION"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("SETTINGS$BUDGETS_DEFAULT_PREVIEW"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/new users/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/keep their current budgets/i),
    ).not.toBeInTheDocument();
  });

  it("confirms a successful organization budget save", async () => {
    const user = userEvent.setup();
    const successToastSpy = vi.spyOn(ToastHandlers, "displaySuccessToast");
    await renderBudgets();

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(successToastSpy).toHaveBeenCalledWith("SETTINGS$SAVED"),
    );
  });

  it("confirms a successful default budget save", async () => {
    const user = userEvent.setup();
    const successToastSpy = vi.spyOn(ToastHandlers, "displaySuccessToast");
    await renderBudgets();
    await user.click(
      screen.getByRole("button", { name: "Default budget for users" }),
    );

    await user.click(screen.getByRole("button", { name: "Update default" }));

    await waitFor(() =>
      expect(successToastSpy).toHaveBeenCalledWith("SETTINGS$SAVED"),
    );
  });

  it("confirms a successful user override save", async () => {
    const user = userEvent.setup();
    const successToastSpy = vi.spyOn(ToastHandlers, "displaySuccessToast");
    await renderBudgets();
    await user.click(screen.getByRole("button", { name: "User overrides" }));
    await user.click(
      screen.getByRole("button", { name: "Edit budget for User One" }),
    );

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(successToastSpy).toHaveBeenCalledWith("SETTINGS$SAVED"),
    );
  });

  it("confirms a successful user override removal", async () => {
    const user = userEvent.setup();
    const successToastSpy = vi.spyOn(ToastHandlers, "displaySuccessToast");
    await renderBudgets();
    await user.click(screen.getByRole("button", { name: "User overrides" }));

    await user.click(screen.getByLabelText("Remove override for User One"));

    await waitFor(() =>
      expect(successToastSpy).toHaveBeenCalledWith("SETTINGS$SAVED"),
    );
  });

  it("does not confirm a budget save that failed", async () => {
    const user = userEvent.setup();
    const successToastSpy = vi.spyOn(ToastHandlers, "displaySuccessToast");
    vi.mocked(organizationService.updateBudgetSettings).mockRejectedValueOnce(
      new Error("503"),
    );
    await renderBudgets();

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(organizationService.getBudgetSettings).toHaveBeenCalledTimes(2),
    );
    expect(successToastSpy).not.toHaveBeenCalled();
  });
});
