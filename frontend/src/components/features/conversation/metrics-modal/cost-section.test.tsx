import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CostSection } from "./cost-section";

describe("CostSection", () => {
  it("renders total cost for priced usage with no cap (cap absent)", () => {
    render(<CostSection cost={1.2345} maxBudgetPerTask={null} />);
    expect(screen.getByText("CONVERSATION$TOTAL_COST")).toBeInTheDocument();
    expect(screen.getByText("$1.2345")).toBeInTheDocument();
    // No "No budget limit" line and no budget progress UI.
    expect(
      screen.queryByText("CONVERSATION$NO_BUDGET_LIMIT"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("CONVERSATION$BUDGET_USAGE_FORMAT"),
    ).not.toBeInTheDocument();
  });

  it("renders total cost as $0.0000 for measured-zero cost with no cap", () => {
    render(<CostSection cost={0} maxBudgetPerTask={null} />);
    expect(screen.getByText("$0.0000")).toBeInTheDocument();
    expect(
      screen.queryByText("CONVERSATION$NO_BUDGET_LIMIT"),
    ).not.toBeInTheDocument();
  });

  it("renders nothing when cost is unavailable (null)", () => {
    const { container } = render(
      <CostSection cost={null} maxBudgetPerTask={null} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the budget progress UI when a real per-conversation cap exists", () => {
    render(<CostSection cost={5} maxBudgetPerTask={25} />);
    // Total cost line still renders.
    expect(screen.getByText("CONVERSATION$TOTAL_COST")).toBeInTheDocument();
    // Usage text (budget progress) is rendered when a cap exists.
    expect(
      screen.getByText("CONVERSATION$BUDGET_USAGE_FORMAT"),
    ).toBeInTheDocument();
  });
});
