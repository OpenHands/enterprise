import { describe, expect, it } from "vitest";
import { isInstanceSuperAdmin } from "#/utils/org/permissions";

describe("isInstanceSuperAdmin", () => {
  it("returns true only for manage_super_admins", () => {
    expect(isInstanceSuperAdmin(["manage_super_admins"])).toBe(true);
    expect(
      isInstanceSuperAdmin(["manage_super_admins", "provision_user"]),
    ).toBe(true);
  });

  it("returns false for org-scoped permissions that look similar", () => {
    // Org owners/admins also get provision_user; that must not open Super Admin.
    expect(isInstanceSuperAdmin(["provision_user"])).toBe(false);
    expect(isInstanceSuperAdmin(["create_organization"])).toBe(false);
    expect(isInstanceSuperAdmin([])).toBe(false);
    expect(isInstanceSuperAdmin(["view_billing"])).toBe(false);
    expect(isInstanceSuperAdmin(undefined)).toBe(false);
    expect(isInstanceSuperAdmin(null)).toBe(false);
  });
});
