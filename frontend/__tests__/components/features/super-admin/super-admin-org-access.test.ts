import { describe, expect, it } from "vitest";
import { superAdminCanViewOrg } from "#/components/features/super-admin/super-admin-org-access";

describe("superAdminCanViewOrg", () => {
  const memberships = [
    { org_id: "2", status: "active" },
    { org_id: "4", status: "inactive" },
  ];

  it("allows an active membership", () => {
    expect(superAdminCanViewOrg(memberships, "2")).toBe(true);
  });

  it("blocks a missing or suspended membership", () => {
    expect(superAdminCanViewOrg(memberships, "3")).toBe(false);
    expect(superAdminCanViewOrg(memberships, "4")).toBe(false);
    expect(superAdminCanViewOrg(undefined, "2")).toBe(false);
  });
});
