import { test, expect, type Page } from "@playwright/test";
import {
  budgetPreview,
  managedBudget,
  pendingOperation,
} from "../__tests__/helpers/budget-control";
import type {
  BudgetAdoptionRequest,
  BudgetNotificationState,
  BudgetNotificationUpdate,
  BudgetOperation,
  BudgetPolicyUpdate,
} from "../src/api/budget-service/budget-service.types";
import type { OrgBudgetSettings } from "../src/api/organization-service/organization-service.api";

type BudgetBrowserRequest = {
  endpoint: string;
  method: string;
  body: unknown;
};
type BudgetBrowserResponse = {
  json?: unknown;
  status?: number;
  lost?: boolean;
};

async function openTeamBudgets(page: Page, reload = false) {
  if (reload) {
    // Reinstall page-bound MSW handlers on a fresh document, retaining sessionStorage.
    await page.evaluate(async () => {
      const browserModule = "/src/mocks/browser.ts";
      const { worker } = await import(browserModule);
      worker.stop();
      const registrations = await navigator.serviceWorker.getRegistrations();
      await Promise.all(
        registrations.map((registration) => registration.unregister()),
      );
    });
    await page.reload();
  } else await page.goto("/settings");
  await page.getByTestId("settings-navbar").waitFor();
  await page.evaluate(async () => {
    const browserModule = "/src/mocks/browser.ts";
    const mswModule = "/node_modules/.vite/deps/msw.js";
    const { worker } = await import(browserModule);
    const { http, HttpResponse } = await import(mswModule);
    const respond = (
      window as typeof window & {
        budgetTestRequest: (
          request: BudgetBrowserRequest,
        ) => Promise<BudgetBrowserResponse>;
      }
    ).budgetTestRequest;
    worker.use(
      http.all(
        /\/api\/organizations\/2\/budgets(?:\/|$)/,
        async ({ request }: { request: Request }) => {
          const response = await respond({
            endpoint: new URL(request.url).pathname,
            method: request.method,
            body:
              request.method === "GET"
                ? null
                : await request.json().catch(() => null),
          });
          return response.lost
            ? HttpResponse.error()
            : HttpResponse.json(response.json, {
                status: response.status ?? 200,
              });
        },
      ),
    );
  });
  await page
    .getByTestId("settings-navbar")
    .getByTestId("org-selector")
    .getByRole("combobox")
    .click();
  await page.getByRole("option", { name: "Acme Corp" }).click();
  await page
    .getByTestId("settings-navbar")
    .getByRole("link", { name: "Budgets", exact: true })
    .click();
}

// Native enforcement and auth are separate release gates; this verifies browser behavior.
test("budget adoption survives response loss and reload, retries, edits, and hands off", async ({
  page,
}, testInfo) => {
  test.setTimeout(90000);
  const serverErrors: string[] = [];
  page.on("response", (response) => {
    if (response.status() >= 500)
      serverErrors.push(`${response.status()} ${response.url()}`);
  });
  let current: OrgBudgetSettings = {
    ...managedBudget,
    control_mode: "needs_adoption",
    control_generation: 0,
    pending_operation_id: null,
    current_cycle_allowance: null,
    current_spend: null,
    current_spend_percentage: null,
    monthly_limit: 1,
    reconciliation_state: "inactive",
    budget_policy_matches: null,
    desired_team_max_budget: null,
    applied_team_max_budget: budgetPreview.team_policy.max_budget,
    users_total: 1,
    users_per_page: 50,
  };
  let original: BudgetAdoptionRequest | null = null;
  let operation: BudgetOperation = pendingOperation;
  let loseFirstResponse = true;
  const submissions: BudgetAdoptionRequest[] = [];
  const updates: BudgetPolicyUpdate[] = [];
  const handoffs: { expected_generation: number }[] = [];
  let notifications: BudgetNotificationState = {
    fingerprint: "a".repeat(64),
    thresholds: [{ percentage: 80, email_enabled: true, slack_enabled: false }],
    slack_channel: null,
    slack_team_id: null,
    email_configured: false,
    slack_account_linked: false,
  };
  const notificationSaves: BudgetNotificationUpdate[] = [];
  await page.exposeFunction(
    "budgetTestRequest",
    async (request: BudgetBrowserRequest): Promise<BudgetBrowserResponse> => {
      const { endpoint } = request;
      if (endpoint.endsWith("/notifications")) {
        if (request.method === "PUT") {
          const body = request.body as BudgetNotificationUpdate;
          expect(body.expected_fingerprint).toBe(notifications.fingerprint);
          notificationSaves.push(body);
          const { expected_fingerprint: _, ...preferences } = body;
          notifications = {
            ...notifications,
            ...preferences,
            fingerprint: String(notificationSaves.length).repeat(64),
          };
        } else expect(request.method).toBe("GET");
        return { json: notifications };
      }
      if (endpoint.endsWith("/adoption/preview"))
        return { json: budgetPreview };
      if (endpoint.endsWith("/adoption") && request.method === "POST") {
        const body = request.body as BudgetAdoptionRequest;
        submissions.push(body);
        original ??= body;
        expect(body).toEqual(original);
        operation = {
          ...operation,
          current_allowances: {
            team: body.current_team_allowance,
            default_member: body.current_default_member_allowance,
            members: body.current_member_allowances ?? {},
          },
          future_policy: {
            monthly_limit: body.future_monthly_limit,
            default_user_monthly_limit: body.future_default_member_limit,
            member_limits: body.future_member_limits ?? {},
            reset_day: body.reset_day,
          },
        };
        current = {
          ...current,
          control_generation: 1,
          pending_operation_id: operation.operation_id,
        };
        if (loseFirstResponse) {
          loseFirstResponse = false;
          return { lost: true };
        }
        return { status: 202, json: operation };
      }
      if (endpoint.endsWith("/retry")) {
        operation = {
          ...operation,
          status: "applied",
          control_mode: "managed",
          error: null,
        };
        current = {
          ...current,
          control_mode: "managed",
          control_generation: 1,
          pending_operation_id: null,
          current_cycle_allowance: original!.current_team_allowance,
          current_cycle_default_member_allowance:
            original!.current_default_member_allowance,
          current_cycle_member_allowances:
            original!.current_member_allowances ?? {},
          monthly_limit: original!.future_monthly_limit,
          default_user_monthly_limit: original!.future_default_member_limit,
          future_member_limits: original!.future_member_limits ?? {},
          current_spend: 0,
          current_spend_percentage: 0,
          desired_team_max_budget:
            budgetPreview.team_spend + original!.current_team_allowance,
          applied_team_max_budget:
            budgetPreview.team_spend + original!.current_team_allowance,
          budget_policy_matches: true,
          reconciliation_state: "healthy",
          users: current.users.map((user) => ({ ...user, current_spend: 0 })),
        };
        return { json: operation };
      }
      if (endpoint.includes("/operations/"))
        return {
          json: operation,
          status: operation.status === "pending" ? 202 : 200,
        };
      if (endpoint.endsWith("/policy") && request.method === "PATCH") {
        const body = request.body as BudgetPolicyUpdate;
        updates.push(body);
        operation = {
          ...operation,
          kind: "settings",
          operation_id: "operation-2",
          generation: 2,
          operation_generation: 2,
          current_allowances: {
            team: body.current_cycle_team_allowance,
            default_member: body.current_cycle_default_member_allowance,
            members: body.current_cycle_member_allowances,
          },
        };
        current = {
          ...current,
          current_cycle_allowance: body.current_cycle_team_allowance,
          monthly_limit: body.future_monthly_limit,
          control_generation: 2,
          desired_team_max_budget:
            budgetPreview.team_spend + body.current_cycle_team_allowance,
          applied_team_max_budget:
            budgetPreview.team_spend + body.current_cycle_team_allowance,
        };
        return { json: operation };
      }
      if (endpoint.endsWith("/handoff")) {
        handoffs.push(request.body as { expected_generation: number });
        current = {
          ...current,
          control_mode: "external",
          control_generation: 3,
        };
        return {
          json: { control_mode: "external", generation: 3 },
        };
      }
      if (endpoint.endsWith("/budgets") && request.method === "GET")
        return { json: current };
      throw new Error(
        `Unexpected budget endpoint: ${request.method} ${endpoint}`,
      );
    },
  );

  await openTeamBudgets(page);
  await expect(
    page.getByRole("heading", { name: "Budget adoption required" }),
  ).toBeVisible();
  await page.getByLabel("Email admins at 80%").uncheck();
  await page
    .getByRole("button", { name: "Save notifications", exact: true })
    .click();
  await expect(page.getByText("Notification preferences saved.")).toBeVisible();
  expect(notificationSaves).toHaveLength(1);
  expect(current.control_generation).toBe(0);
  expect(current.monthly_limit).toBe(1);
  expect(submissions).toHaveLength(0);
  await page
    .getByRole("button", { name: "Review current LiteLLM policy" })
    .click();
  const team = page.getByRole("group", { name: "Organization", exact: true });
  const defaults = page.getByRole("group", {
    name: "Default per member",
    exact: true,
  });
  await expect(team.getByLabel("Available to spend now (USD)")).toBeEmpty();
  await team.getByLabel("Available to spend now (USD)").fill("10");
  await team.getByLabel("Recurring monthly allowance (USD)").fill("1000");
  await defaults.getByLabel("Available to spend now (USD)").fill("5");
  await defaults.getByLabel("Recurring monthly allowance (USD)").fill("100");
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(
    page.getByText(
      "The original request is saved, but its result is unknown.",
      { exact: false },
    ),
  ).toBeVisible();
  expect(submissions).toHaveLength(1);

  await openTeamBudgets(page, true);
  await expect(
    page.getByText(
      "The original request is saved, but its result is unknown.",
      { exact: false },
    ),
  ).toBeVisible();
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(
    page.getByText("Budget changes are pending.", { exact: false }),
  ).toBeVisible();
  expect(submissions).toHaveLength(2);
  expect(submissions[0]).toEqual(submissions[1]);
  await expect(
    page.getByText("Budget changes verified.", { exact: true }),
  ).toBeHidden();
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(
    page.getByText("Budget changes verified.", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("adoption-verified.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await team
    .getByLabel("Total allowance for this cycle (USD, including already spent)")
    .fill("15");
  await page.getByRole("button", { name: "Save Changes", exact: true }).click();
  await expect(
    page.getByText("Budget changes verified.", { exact: true }),
  ).toBeVisible();
  expect(updates).toHaveLength(1);
  expect(updates[0]).toMatchObject({
    expected_generation: 1,
    current_cycle_team_allowance: 15,
    future_monthly_limit: 1000,
  });
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page
    .getByRole("checkbox", {
      name: "Stop OpenHands budget management",
      exact: false,
    })
    .check();
  await page
    .getByRole("button", { name: "Hand off budget control", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Managed outside OpenHands" }),
  ).toBeVisible();
  expect(handoffs).toEqual([{ expected_generation: 2 }]);
  expect(current.current_cycle_allowance).toBe(15);
  await page.getByLabel("Email admins at 80%").check();
  await page
    .getByRole("button", { name: "Save notifications", exact: true })
    .click();
  await expect(page.getByText("Notification preferences saved.")).toBeVisible();
  expect(notificationSaves).toHaveLength(2);
  expect(current.control_generation).toBe(3);
  expect(current.control_mode).toBe("external");
  expect(updates).toHaveLength(1);
  await page.screenshot({
    path: testInfo.outputPath("handoff.png"),
    fullPage: true,
  });
  expect(serverErrors).toEqual([]);
});
