import { describe, expect, it } from "vitest";
import { isInstanceSuperAdmin } from "#/utils/org/permissions";

describe("isInstanceSuperAdmin", () => {
  it("returns true for any instance Super Admin permission", () => {
    expect(isInstanceSuperAdmin(["create_organization"])).toBe(true);
    expect(isInstanceSuperAdmin(["provision_user"])).toBe(true);
    expect(isInstanceSuperAdmin(["manage_super_admins"])).toBe(true);
  });

  it("returns false without instance permissions", () => {
    expect(isInstanceSuperAdmin([])).toBe(false);
    expect(isInstanceSuperAdmin(["view_billing"])).toBe(false);
    expect(isInstanceSuperAdmin(undefined)).toBe(false);
    expect(isInstanceSuperAdmin(null)).toBe(false);
  });
});
