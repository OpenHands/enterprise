import { describe, expect, it } from "vitest";
import { getSuperAdminUsageView } from "#/components/features/super-admin/super-admin-usage-data";

describe("getSuperAdminUsageView", () => {
  it("aggregates every organization by default", () => {
    const all = getSuperAdminUsageView([], "30d");
    const acme = getSuperAdminUsageView(["2"], "30d");
    const hands = getSuperAdminUsageView(["4"], "30d");

    expect(all.snapshots).toHaveLength(4);
    expect(all.conversations).toBeGreaterThan(acme.conversations);
    expect(all.spend).toBeCloseTo(acme.spend + hands.spend + 1480 + 620, 1);
    expect(all.users.some((user) => user.org_name === "Acme Corp")).toBe(true);
    expect(all.users.some((user) => user.org_name === "All Hands AI")).toBe(
      true,
    );
  });

  it("compares only the selected organizations", () => {
    const compared = getSuperAdminUsageView(["2", "4"], "30d");

    expect(compared.snapshots.map((row) => row.orgName)).toEqual([
      "Acme Corp",
      "All Hands AI",
    ]);
    expect(
      compared.conversationRows.every(
        (row) =>
          row.org_name === "Acme Corp" || row.org_name === "All Hands AI",
      ),
    ).toBe(true);
    expect(compared.conversations).toBe(
      compared.snapshots.reduce((sum, row) => sum + row.conversations, 0),
    );
  });
});
