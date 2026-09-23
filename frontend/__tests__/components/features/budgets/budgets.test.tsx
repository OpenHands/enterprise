import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { AxiosError } from "axios";
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
    expect(input).toHaveValue(500);
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
    const input = screen.getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "75");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/Budget change wasn't saved/);
    expect(input).toHaveValue(75);
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument());
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
    await user.clear(screen.getByRole("spinbutton"));
    await user.type(screen.getByRole("spinbutton"), "75");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/Budget change wasn't saved/);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/Budget change wasn't saved/)).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("Edit budget for User One"));
    expect(screen.getByRole("spinbutton")).toHaveValue(50);
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
      reconciliation_state: "degraded",
      reconciliation_error: "member cycle baseline is unavailable",
    });

    await renderBudgets();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Degraded — The last budget update could not be completed or verified.",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "member cycle baseline is unavailable",
    );
    expect(screen.getByText("$200.00")).toBeInTheDocument();
  });

  it("explains a team cap that includes spend recorded before the cycle started", async () => {
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      desired_team_max_budget: 1770,
      applied_team_max_budget: 1770,
    });

    await renderBudgets();

    expect(
      screen.getByText(
        "The $1,770 team cap includes the $770.00 already recorded before this cycle started.",
      ),
    ).toBeInTheDocument();
  });

  it("explains the team cap without an amount when it equals the monthly limit", async () => {
    await renderBudgets();

    expect(
      screen.getByText(
        "The team cap includes any spend already recorded before this cycle started.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/team cap includes the \$/),
    ).not.toBeInTheDocument();
  });

  it("omits the team cap explanation when no organization budget is enforced", async () => {
    vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
      ...budgetResponse,
      enabled: false,
      reconciliation_state: "inactive",
      desired_team_max_budget: null,
      applied_team_max_budget: null,
    });

    await renderBudgets();

    expect(
      screen.queryByText(/already recorded before this cycle started/),
    ).not.toBeInTheDocument();
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
    const overrideInput = screen.getByRole("spinbutton");
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
});
