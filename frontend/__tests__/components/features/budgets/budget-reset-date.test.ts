import { describe, expect, it } from "vitest";
import { nextBudgetResetDate } from "#/components/features/budgets/budget-reset-date";

describe("budget reset preview", () => {
  it.each([
    [15, "2026-09-10T12:00:00Z", "2026-09-15T00:00:00.000Z"],
    [1, "2026-09-10T12:00:00Z", "2026-10-01T00:00:00.000Z"],
    [15, "2026-09-14T20:00:00-04:00", "2026-10-15T00:00:00.000Z"],
    [1, "2026-12-31T23:59:59Z", "2027-01-01T00:00:00.000Z"],
    [1, "2028-02-29T23:59:59Z", "2028-03-01T00:00:00.000Z"],
  ])("uses the next future UTC boundary (%s, %s)", (day, now, expected) => {
    expect(nextBudgetResetDate(day, 0, undefined, new Date(now)).toISOString()).toBe(expected);
  });

  it("keeps a saved reset when maintenance has not yet processed it", () => {
    expect(nextBudgetResetDate(1, 1, "2026-10-01T00:00:00Z", new Date("2026-10-02"))
      .toISOString()).toBe("2026-10-01T00:00:00.000Z");
  });
});
