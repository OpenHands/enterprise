import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BudgetDisplay } from "./budget-display";

describe("BudgetDisplay", () => {
  it("renders nothing when cost is null (unavailable metrics)", () => {
    const { container } = render(
      <BudgetDisplay cost={null} maxBudgetPerTask={null} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when no per-conversation cap is set (cap absent)", () => {
    // max_budget_per_task is never populated in the current setup; the
    // misleading "No budget limit" line must NOT be shown.
    const { container } = render(
      <BudgetDisplay cost={0} maxBudgetPerTask={null} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when cap is zero or negative (cap absent)", () => {
    const { container } = render(
      <BudgetDisplay cost={1.5} maxBudgetPerTask={0} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the progress bar and usage text when a real cap exists", () => {
    const { container } = render(
      <BudgetDisplay cost={5} maxBudgetPerTask={25} />,
    );
    // Progress bar fill: 5/25 = 20%.
    const fill = container.querySelector(
      ".h-full.transition-all.duration-300",
    ) as HTMLElement;
    expect(fill).not.toBeNull();
    expect(fill.style.width).toBe("20%");
    // Usage text is rendered (mock i18n returns the key).
    expect(
      screen.getByText("CONVERSATION$BUDGET_USAGE_FORMAT"),
    ).toBeInTheDocument();
  });
});
