import { describe, expect, it } from "vitest";
import { canAccessSuperAdminDashboard } from "#/utils/org/super-admin-access";

describe("canAccessSuperAdminDashboard", () => {
  it("requires the feature flag and manage_super_admins", () => {
    expect(
      canAccessSuperAdminDashboard(
        { enable_super_admin: true } as never,
        ["manage_super_admins"],
      ),
    ).toBe(true);
    expect(
      canAccessSuperAdminDashboard(
        { enable_super_admin: false } as never,
        ["manage_super_admins"],
      ),
    ).toBe(false);
    expect(
      canAccessSuperAdminDashboard(
        { enable_super_admin: true } as never,
        [],
      ),
    ).toBe(false);
    expect(
      canAccessSuperAdminDashboard(
        { enable_super_admin: true } as never,
        ["provision_user", "create_organization"],
      ),
    ).toBe(false);
    expect(
      canAccessSuperAdminDashboard(undefined, ["manage_super_admins"]),
    ).toBe(false);
  });
});
