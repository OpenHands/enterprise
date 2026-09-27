import { YourBudget } from "#/components/features/your-budget/your-budget";

// The page renders its own header: the shared one cannot interpolate the
// organization name into the subtitle.
export const handle = { hideTitle: true };

export default function YourBudgetRoute() {
  return <YourBudget />;
}
