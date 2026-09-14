import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { BudgetPolicyForm } from "#/components/features/budgets/budget-policy-form";
import {
  buildBudgetSubmission,
  initialBudgetDraft,
} from "#/components/features/budgets/budget-policy-draft";
import { budgetPreview, managedBudget } from "../../../helpers/budget-control";

it("does not seed adoption allowances from legacy $1 or absolute native caps", () => {
  render(
    <BudgetPolicyForm
      source={{ preview: budgetPreview }}
      disabled={false}
      onSubmit={vi.fn()}
    />,
  );
  const team = within(
    screen.getByRole("group", { name: "BUDGET_CONTROL$ORGANIZATION" }),
  );
  expect(team.getByLabelText("BUDGET_CONTROL$AVAILABLE_NOW")).toHaveValue(null);
  expect(team.getByLabelText("BUDGET_CONTROL$FUTURE_ALLOWANCE")).toHaveValue(
    null,
  );
});

it("requires explicit member policy and sends separate remaining and recurring allowances", async () => {
  const user = userEvent.setup();
  const submit = vi.fn();
  const { container } = render(
    <BudgetPolicyForm
      source={{ preview: budgetPreview }}
      disabled={false}
      onSubmit={submit}
    />,
  );
  const team = within(
    screen.getByRole("group", { name: "BUDGET_CONTROL$ORGANIZATION" }),
  );
  const member = within(
    screen.getByRole("group", { name: "BUDGET_CONTROL$DEFAULT_MEMBER" }),
  );
  await user.type(team.getByLabelText("BUDGET_CONTROL$AVAILABLE_NOW"), "0");
  await user.type(
    team.getByLabelText("BUDGET_CONTROL$FUTURE_ALLOWANCE"),
    "1000",
  );
  fireEvent.submit(container.querySelector("form")!);
  expect(submit).not.toHaveBeenCalled();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "BUDGET_CONTROL$INVALID_ALLOWANCE",
  );
  await user.click(
    member.getAllByLabelText("BUDGET_CONTROL$NO_MEMBER_LIMIT")[0],
  );
  await user.type(
    member.getByLabelText("BUDGET_CONTROL$FUTURE_ALLOWANCE"),
    "100",
  );
  await user.selectOptions(
    screen.getByLabelText("BUDGET_CONTROL$RESET_DAY"),
    "15",
  );
  await user.click(screen.getByLabelText("BUDGET_CONTROL$REPLACE_RESETS"));
  await user.click(screen.getByRole("button", { name: "BUTTON$CONFIRM" }));
  expect(submit).toHaveBeenCalledExactlyOnceWith({
    kind: "adopt",
    request: {
      idempotency_key: expect.any(String),
      preview_fingerprint: budgetPreview.fingerprint,
      current_team_allowance: 0,
      current_default_member_allowance: null,
      current_member_allowances: {},
      future_monthly_limit: 1000,
      future_default_member_limit: 100,
      future_member_limits: {},
      reset_day: 15,
      replace_native_reset_schedules: true,
    },
  });
});

it("keeps cycle totals, off-page overrides and reviewed generation on a settings edit", async () => {
  const user = userEvent.setup();
  const submit = vi.fn();
  const view = render(
    <BudgetPolicyForm
      source={{ budget: managedBudget }}
      disabled={false}
      onSubmit={submit}
    />,
  );
  const team = within(
    screen.getByRole("group", { name: "BUDGET_CONTROL$ORGANIZATION" }),
  );
  expect(team.getByLabelText("BUDGET_CONTROL$CYCLE_TOTAL")).toHaveValue(100);
  await user.clear(team.getByLabelText("BUDGET_CONTROL$CYCLE_TOTAL"));
  await user.type(team.getByLabelText("BUDGET_CONTROL$CYCLE_TOTAL"), "150");
  view.rerender(
    <BudgetPolicyForm
      source={{
        budget: {
          ...managedBudget,
          control_generation: 2,
          current_cycle_allowance: 300,
        },
      }}
      disabled={false}
      onSubmit={submit}
    />,
  );
  await user.click(
    screen.getByRole("button", { name: "SETTINGS$SAVE_CHANGES" }),
  );
  expect(submit.mock.calls[0][0]).toMatchObject({
    kind: "settings",
    request: {
      expected_generation: 1,
      current_cycle_team_allowance: 150,
      current_cycle_member_allowances: { "off-page": 0 },
      future_member_limits: { "off-page": 100 },
      future_monthly_limit: 700,
    },
  });
});

it("blocks submissions while another operation is pending", () => {
  const submit = vi.fn();
  const { container } = render(
    <BudgetPolicyForm
      source={{ budget: managedBudget }}
      disabled
      onSubmit={submit}
    />,
  );
  expect(
    screen.getByRole("button", { name: "SETTINGS$SAVE_CHANGES" }),
  ).toBeDisabled();
  fireEvent.submit(container.querySelector("form")!);
  expect(submit).not.toHaveBeenCalled();
});

it.each(["", " ", "-1", "NaN", "Infinity", "1e999", "0x10"])(
  "rejects invalid current allowance %j",
  (value) => {
    expect(() =>
      buildBudgetSubmission(
        { ...initialBudgetDraft(managedBudget), currentTeam: value },
        "edit",
        { budget: managedBudget },
      ),
    ).toThrow();
  },
);

it("keeps inherited, explicit unlimited and explicit zero member policies distinct", () => {
  const result = buildBudgetSubmission(
    {
      ...initialBudgetDraft(managedBudget),
      currentMembers: { inherited: undefined, unlimited: null, denied: "0" },
      futureMembers: { inherited: undefined, unlimited: null, limited: "10" },
    },
    "edit",
    { budget: managedBudget },
  );
  expect(result).toMatchObject({
    kind: "settings",
    request: {
      current_cycle_member_allowances: { unlimited: null, denied: 0 },
      future_member_limits: { unlimited: null, limited: 10 },
    },
  });
});

it("does not interpret a recurring zero limit as unlimited", () => {
  expect(() =>
    buildBudgetSubmission(
      { ...initialBudgetDraft(managedBudget), futureDefault: "0" },
      "edit",
      { budget: managedBudget },
    ),
  ).toThrow();
});
