import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Budgets } from "#/components/features/budgets/budgets";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { budgetService } from "#/api/budget-service/budget-service.api";
import {
  readBudgetSubmission,
  saveBudgetSubmission,
} from "#/utils/budget-submission";
import {
  adoptionRequest,
  budgetPreview,
  managedBudget,
  pendingOperation,
} from "../../../helpers/budget-control";

const selected = vi.hoisted(() => ({ organizationId: "org-a" }));
vi.mock("#/context/use-selected-organization", () => ({
  useSelectedOrganizationId: () => selected,
}));
vi.mock("#/hooks/use-debounce", () => ({
  useDebounce: (value: string) => value,
}));

const clients: QueryClient[] = [];
function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, ...render(<Budgets />, { wrapper }) };
}

beforeEach(() => {
  selected.organizationId = "org-a";
  vi.spyOn(budgetService, "getNotifications").mockResolvedValue({
    fingerprint: "a".repeat(64),
    thresholds: [],
    slack_channel: null,
    slack_team_id: null,
    email_configured: false,
    slack_account_linked: false,
  });
  vi.spyOn(organizationService, "getBudgetSettings").mockResolvedValue(
    managedBudget,
  );
  vi.spyOn(organizationService, "updateBudgetSettings");
  vi.spyOn(organizationService, "upsertBudgetOverride");
  vi.spyOn(organizationService, "deleteBudgetOverride");
});
afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  sessionStorage.clear();
  vi.restoreAllMocks();
});

async function fillAdoption() {
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "BUDGET_CONTROL$PREVIEW" }),
  );
  const team = within(
    await screen.findByRole("group", { name: "BUDGET_CONTROL$ORGANIZATION" }),
  );
  const member = within(
    screen.getByRole("group", { name: "BUDGET_CONTROL$DEFAULT_MEMBER" }),
  );
  await user.type(team.getByLabelText("BUDGET_CONTROL$AVAILABLE_NOW"), "10");
  await user.type(
    team.getByLabelText("BUDGET_CONTROL$FUTURE_ALLOWANCE"),
    "1000",
  );
  await user.type(member.getByLabelText("BUDGET_CONTROL$AVAILABLE_NOW"), "5");
  await user.type(
    member.getByLabelText("BUDGET_CONTROL$FUTURE_ALLOWANCE"),
    "100",
  );
  return user;
}

it("keeps unadopted organizations read-only until an explicit preview and confirmation", async () => {
  const user = userEvent.setup();
  vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
    ...managedBudget,
    control_mode: "needs_adoption",
    control_generation: 0,
    monthly_limit: 1,
  });
  const preview = vi
    .spyOn(budgetService, "preview")
    .mockResolvedValue(budgetPreview);
  const confirm = vi.spyOn(budgetService, "confirm");
  setup();
  await screen.findByText("BUDGET_CONTROL$NEEDS_ADOPTION");
  expect(preview).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("button", { name: "SETTINGS$SAVE_CHANGES" }),
  ).not.toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: "BUDGET_CONTROL$PREVIEW" }),
  );
  const team = within(
    await screen.findByRole("group", { name: "BUDGET_CONTROL$ORGANIZATION" }),
  );
  expect(team.getByLabelText("BUDGET_CONTROL$AVAILABLE_NOW")).toHaveValue(null);
  expect(team.getByLabelText("BUDGET_CONTROL$FUTURE_ALLOWANCE")).toHaveValue(
    null,
  );
  expect(confirm).not.toHaveBeenCalled();
  expect(organizationService.updateBudgetSettings).not.toHaveBeenCalled();
});

it("confirms adoption, reports pending and retries the same operation before showing success", async () => {
  vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
    ...managedBudget,
    control_mode: "needs_adoption",
    control_generation: 0,
  });
  vi.spyOn(budgetService, "preview").mockResolvedValue(budgetPreview);
  const confirm = vi
    .spyOn(budgetService, "confirm")
    .mockResolvedValue(pendingOperation);
  vi.spyOn(budgetService, "getOperation").mockResolvedValue(pendingOperation);
  const retry = vi
    .spyOn(budgetService, "retry")
    .mockImplementation(async () => {
      vi.mocked(organizationService.getBudgetSettings).mockResolvedValue(
        managedBudget,
      );
      return {
        ...pendingOperation,
        status: "applied",
        control_mode: "managed",
        error: null,
      };
    });
  setup();
  const user = await fillAdoption();
  await user.click(screen.getByRole("button", { name: "BUTTON$CONFIRM" }));
  await screen.findByText("BUDGET_CONTROL$PENDING");
  expect(screen.queryByText("BUDGET_CONTROL$APPLIED")).not.toBeInTheDocument();
  expect(confirm.mock.calls[0][0].request).toMatchObject({
    preview_fingerprint: budgetPreview.fingerprint,
    current_team_allowance: 10,
    future_monthly_limit: 1000,
    current_default_member_allowance: 5,
    future_default_member_limit: 100,
    replace_native_reset_schedules: false,
  });
  await user.click(screen.getByRole("button", { name: "CONVERSATION$RETRY" }));
  await screen.findByText("BUDGET_CONTROL$APPLIED");
  expect(retry.mock.calls[0][0]).toEqual({
    orgId: "org-a",
    operationId: "operation-1",
  });
  await user.click(screen.getByRole("button", { name: "DEVICE$CONTINUE" }));
  expect(readBudgetSubmission("org-a")).toBeNull();
  await screen.findByRole("button", { name: "SETTINGS$SAVE_CHANGES" });
});

it("uses the versioned policy endpoint and preserves off-page overrides", async () => {
  const user = userEvent.setup();
  const update = vi.spyOn(budgetService, "update").mockResolvedValue({
    ...pendingOperation,
    kind: "settings",
    control_mode: "managed",
  });
  vi.spyOn(budgetService, "getOperation").mockResolvedValue(pendingOperation);
  setup();
  const team = within(
    await screen.findByRole("group", { name: "BUDGET_CONTROL$ORGANIZATION" }),
  );
  await user.clear(team.getByLabelText("BUDGET_CONTROL$CYCLE_TOTAL"));
  await user.type(team.getByLabelText("BUDGET_CONTROL$CYCLE_TOTAL"), "150");
  await user.click(
    screen.getByRole("button", { name: "SETTINGS$SAVE_CHANGES" }),
  );
  await waitFor(() => expect(update).toHaveBeenCalledOnce());
  expect(update.mock.calls[0][0]).toMatchObject({
    orgId: "org-a",
    request: {
      expected_generation: 1,
      current_cycle_team_allowance: 150,
      future_monthly_limit: 700,
      current_cycle_member_allowances: { "off-page": 0 },
      future_member_limits: { "off-page": 100 },
    },
  });
  expect(organizationService.updateBudgetSettings).not.toHaveBeenCalled();
  expect(organizationService.upsertBudgetOverride).not.toHaveBeenCalled();
});

it("recovers saved uncertain intent after a browser reload without creating new allowance", async () => {
  const user = userEvent.setup();
  saveBudgetSubmission("org-a", {
    submission: { kind: "adopt", request: adoptionRequest },
    operationId: null,
  });
  vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
    ...managedBudget,
    pending_operation_id: "operation-1",
  });
  const retry = vi.spyOn(budgetService, "retry");
  const confirm = vi
    .spyOn(budgetService, "confirm")
    .mockRejectedValue(new Error("Response lost"));
  setup();
  await screen.findByText("BUDGET_CONTROL$UNCERTAIN_REQUEST");
  expect(
    screen.getAllByRole("button", { name: "CONVERSATION$RETRY" }),
  ).toHaveLength(1);
  expect(
    screen.queryByRole("button", { name: "SETTINGS$SAVE_CHANGES" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "CONVERSATION$RETRY" }));
  await waitFor(() =>
    expect(confirm).toHaveBeenCalledExactlyOnceWith({
      orgId: "org-a",
      request: adoptionRequest,
    }),
  );
  expect(readBudgetSubmission("org-a")?.submission.request).toEqual(
    adoptionRequest,
  );
  expect(retry).not.toHaveBeenCalled();
});

it("shows unavailable spend instead of a fabricated zero", async () => {
  vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
    ...managedBudget,
    spend_status: "unavailable",
    current_spend: null,
    current_spend_percentage: null,
    users: [{ ...managedBudget.users[0], current_spend: null }],
  });
  setup();
  await screen.findByText(/Spend data is temporarily unavailable/);
  expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
});

it("explains governed SDK usage and unmapped native identities", async () => {
  vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
    ...managedBudget,
    unmapped_member_count: 1,
    unmapped_spend: 12.5,
  });
  setup();
  await screen.findByText(/SDK requests routed through this deployment/);
  expect(
    screen.getByText(/1 LiteLLM identity is not mapped/),
  ).toHaveTextContent("$12.50 of this cycle's spend");
});

it("retains authoritative spend when reconciliation fails", async () => {
  vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
    ...managedBudget,
    reconciliation_state: "degraded",
    reconciliation_error: "member cycle baseline is unavailable",
  });
  setup();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "member cycle baseline is unavailable",
  );
  expect(screen.getByText("$20.00")).toBeVisible();
});

it("blocks editing when the budget state cannot be read", async () => {
  vi.mocked(organizationService.getBudgetSettings).mockRejectedValue(
    new Error("Unavailable"),
  );
  setup();
  await screen.findByText("BUDGET_CONTROL$STATUS_UNAVAILABLE");
  expect(
    screen.queryByRole("button", { name: "SETTINGS$SAVE_CHANGES" }),
  ).not.toBeInTheDocument();
});

it("requires explicit handoff consent and sends the reviewed generation", async () => {
  const user = userEvent.setup();
  const handoff = vi
    .spyOn(budgetService, "handOff")
    .mockImplementation(async () => {
      vi.mocked(organizationService.getBudgetSettings).mockResolvedValue({
        ...managedBudget,
        control_mode: "external",
        control_generation: 2,
      });
      return { control_mode: "external", generation: 2 };
    });
  setup();
  const button = await screen.findByRole("button", {
    name: "BUDGET_CONTROL$HANDOFF",
  });
  expect(button).toBeDisabled();
  await user.click(screen.getByLabelText("BUDGET_CONTROL$HANDOFF_WARNING"));
  await user.click(button);
  await screen.findByText("BUDGET_CONTROL$EXTERNAL");
  expect(handoff.mock.calls[0][0]).toEqual({
    orgId: "org-a",
    request: { expected_generation: 1 },
  });
  expect(organizationService.updateBudgetSettings).not.toHaveBeenCalled();
});

it("does not move unsaved edits to another selected organization", async () => {
  const user = userEvent.setup();
  const view = setup();
  const team = within(
    await screen.findByRole("group", { name: "BUDGET_CONTROL$ORGANIZATION" }),
  );
  await user.clear(team.getByLabelText("BUDGET_CONTROL$CYCLE_TOTAL"));
  await user.type(team.getByLabelText("BUDGET_CONTROL$CYCLE_TOTAL"), "999");
  selected.organizationId = "org-b";
  view.rerender(<Budgets />);
  await waitFor(() =>
    expect(organizationService.getBudgetSettings).toHaveBeenCalledWith(
      expect.objectContaining({ orgId: "org-b" }),
    ),
  );
  const newTeam = within(
    await screen.findByRole("group", { name: "BUDGET_CONTROL$ORGANIZATION" }),
  );
  expect(newTeam.getByLabelText("BUDGET_CONTROL$CYCLE_TOTAL")).toHaveValue(100);
});

it("keeps edits while moving through member pages", async () => {
  const user = userEvent.setup();
  vi.mocked(organizationService.getBudgetSettings).mockImplementation(
    async ({ usersPage }) => ({
      ...managedBudget,
      users_page: usersPage ?? 1,
      users:
        usersPage === 2
          ? [
              {
                ...managedBudget.users[0],
                user_id: "off-page",
                user_name: "Second Page",
              },
            ]
          : managedBudget.users,
    }),
  );
  setup();
  const team = within(
    await screen.findByRole("group", { name: "BUDGET_CONTROL$ORGANIZATION" }),
  );
  await user.clear(team.getByLabelText("BUDGET_CONTROL$CYCLE_TOTAL"));
  await user.type(team.getByLabelText("BUDGET_CONTROL$CYCLE_TOTAL"), "150");
  await user.click(screen.getByRole("button", { name: "ORG$NEXT" }));
  await screen.findByRole("cell", { name: "Second Page" });
  expect(team.getByLabelText("BUDGET_CONTROL$CYCLE_TOTAL")).toHaveValue(150);
});

it("does not clear an unresolved request when handoff fails", async () => {
  const user = userEvent.setup();
  saveBudgetSubmission("org-a", {
    submission: { kind: "adopt", request: adoptionRequest },
    operationId: null,
  });
  vi.spyOn(budgetService, "handOff").mockRejectedValue(
    new Error("Response lost"),
  );
  setup();
  await user.click(
    await screen.findByLabelText("BUDGET_CONTROL$HANDOFF_WARNING"),
  );
  await user.click(
    screen.getByRole("button", { name: "BUDGET_CONTROL$HANDOFF" }),
  );
  await screen.findByText("BUDGET_CONTROL$REQUEST_ERROR");
  expect(readBudgetSubmission("org-a")?.submission.request).toEqual(
    adoptionRequest,
  );
});
