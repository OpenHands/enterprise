import React from "react";
import { BudgetProgressBar } from "./budget-progress-bar";
import { BudgetUsageText } from "./budget-usage-text";

interface BudgetDisplayProps {
  cost: number | null;
  maxBudgetPerTask: number | null;
}

export function BudgetDisplay({ cost, maxBudgetPerTask }: BudgetDisplayProps) {
  // Only render the per-conversation cap/progress UI when a real cap exists.
  // max_budget_per_task is a pre-V1 field that is never populated in the
  // current setup and cannot be set by users, so absent caps render nothing
  // rather than a misleading "No budget limit" line. Org/user monthly
  // budgets are a different scope and are not shown here.
  if (cost === null || maxBudgetPerTask === null || maxBudgetPerTask <= 0) {
    return null;
  }

  return (
    <div className="border-b border-neutral-700">
      <BudgetProgressBar currentCost={cost} maxBudget={maxBudgetPerTask} />
      <BudgetUsageText currentCost={cost} maxBudget={maxBudgetPerTask} />
    </div>
  );
}
