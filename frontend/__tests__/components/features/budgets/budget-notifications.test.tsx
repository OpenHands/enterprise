import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BudgetNotifications } from "#/components/features/budgets/budget-notifications";
import { budgetService } from "#/api/budget-service/budget-service.api";
import type { BudgetNotificationState } from "#/api/budget-service/budget-service.types";
import { budgetNotificationKey } from "#/hooks/query/use-budget-control";

const initial: BudgetNotificationState = {
  fingerprint: "a".repeat(64),
  thresholds: [{ percentage: 80, email_enabled: true, slack_enabled: false }],
  slack_channel: null,
  slack_team_id: null,
  email_configured: false,
  slack_account_linked: false,
};
const clients: QueryClient[] = [];
function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return {
    client,
    ...render(<BudgetNotifications orgId="org-a" />, { wrapper }),
  };
}

beforeEach(() => {
  vi.spyOn(budgetService, "getNotifications").mockResolvedValue(initial);
  vi.spyOn(budgetService, "updateNotifications").mockImplementation(
    async ({ request }) => ({
      ...initial,
      ...request,
      fingerprint: "b".repeat(64),
    }),
  );
  vi.spyOn(budgetService, "confirm");
  vi.spyOn(budgetService, "update");
});
afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
});

it("saves only alert preferences and advances the reviewed fingerprint", async () => {
  const user = userEvent.setup();
  setup();
  await user.click(await screen.findByLabelText("Email admins at 80%"));
  await user.click(screen.getByRole("button", { name: "Add alert threshold" }));
  await user.click(screen.getByRole("button", { name: "Save notifications" }));
  await screen.findByText("Notification preferences saved.");
  expect(vi.mocked(budgetService.updateNotifications).mock.calls[0][0]).toEqual(
    {
      orgId: "org-a",
      request: {
        expected_fingerprint: initial.fingerprint,
        thresholds: [
          { percentage: 80, email_enabled: false, slack_enabled: false },
          { percentage: 90, email_enabled: true, slack_enabled: false },
        ],
        slack_channel: null,
        slack_team_id: null,
      },
    },
  );
  await user.click(screen.getByLabelText("Email admins at 90%"));
  expect(
    screen.queryByText("Notification preferences saved."),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Save notifications" }));
  await waitFor(() =>
    expect(budgetService.updateNotifications).toHaveBeenCalledTimes(2),
  );
  expect(
    vi.mocked(budgetService.updateNotifications).mock.calls[1][0].request
      .expected_fingerprint,
  ).toBe("b".repeat(64));
  expect(budgetService.confirm).not.toHaveBeenCalled();
  expect(budgetService.update).not.toHaveBeenCalled();
});

it("reports an unconfigured channel and prevents enabling unlinked Slack", async () => {
  setup();
  expect(await screen.findByLabelText("Slack at 80%")).toBeDisabled();
  expect(
    screen.getByText("Email delivery is not configured on this installation."),
  ).toBeInTheDocument();
  expect(screen.getByText(/first link your Slack account/)).toBeInTheDocument();
});

it("rejects duplicate thresholds before sending", async () => {
  const user = userEvent.setup();
  setup();
  await user.click(
    await screen.findByRole("button", { name: "Add alert threshold" }),
  );
  const second = screen.getByLabelText("Alert threshold 2");
  await user.clear(second);
  await user.type(second, "80");
  expect(
    screen.getByRole("button", { name: "Save notifications" }),
  ).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "unique whole-number thresholds",
  );
  expect(budgetService.updateNotifications).not.toHaveBeenCalled();
});

it("retains a failed save for explicit retry without an automatic request", async () => {
  const user = userEvent.setup();
  vi.mocked(budgetService.updateNotifications).mockRejectedValueOnce(
    new Error("response lost"),
  );
  setup();
  await user.click(await screen.findByLabelText("Email admins at 80%"));
  await user.click(screen.getByRole("button", { name: "Save notifications" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Email admins at 80%")).not.toBeChecked();
  expect(budgetService.updateNotifications).toHaveBeenCalledTimes(1);
  const first = vi.mocked(budgetService.updateNotifications).mock.calls[0][0];
  await user.click(screen.getByRole("button", { name: "Save notifications" }));
  await screen.findByText("Notification preferences saved.");
  expect(vi.mocked(budgetService.updateNotifications).mock.calls[1][0]).toEqual(
    first,
  );
});

it("requires explicit reload after a conflict and makes discarding edits clear", async () => {
  const user = userEvent.setup();
  const conflict = new AxiosError("conflict");
  conflict.response = { status: 409 } as AxiosError["response"];
  vi.mocked(budgetService.updateNotifications).mockRejectedValue(conflict);
  setup();
  await user.click(await screen.findByLabelText("Email admins at 80%"));
  await user.click(screen.getByRole("button", { name: "Save notifications" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Alert preferences changed",
  );
  vi.mocked(budgetService.getNotifications).mockResolvedValue({
    ...initial,
    fingerprint: "c".repeat(64),
  });
  await user.click(
    screen.getByRole("button", {
      name: "Reload saved preferences (discards edits)",
    }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Email admins at 80%")).toBeChecked(),
  );
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("keeps an in-flight save scoped to its original organization", async () => {
  const user = userEvent.setup();
  let finish!: (state: BudgetNotificationState) => void;
  vi.mocked(budgetService.updateNotifications).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const { rerender, client } = setup();
  await user.click(await screen.findByLabelText("Email admins at 80%"));
  await user.click(screen.getByRole("button", { name: "Save notifications" }));
  rerender(<BudgetNotifications orgId="org-b" />);
  await waitFor(() =>
    expect(screen.getByLabelText("Email admins at 80%")).toBeChecked(),
  );
  finish({ ...initial, fingerprint: "b".repeat(64) });
  await waitFor(() =>
    expect(
      client.getQueryData<BudgetNotificationState>(
        budgetNotificationKey("org-a"),
      )?.fingerprint,
    ).toBe("b".repeat(64)),
  );
  expect(client.getQueryData(budgetNotificationKey("org-b"))).toEqual(initial);
  expect(screen.getByLabelText("Email admins at 80%")).toBeChecked();
  expect(budgetService.updateNotifications).toHaveBeenCalledTimes(1);
});
