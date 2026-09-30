import { describe, expect, it } from "vitest";
import { canAccessSuperAdminDashboard } from "#/utils/org/super-admin-access";

describe("canAccessSuperAdminDashboard", () => {
  it("requires the feature flag and an instance Super Admin permission", () => {
    expect(
      canAccessSuperAdminDashboard(
        { enable_super_admin: true } as never,
        ["create_organization"],
      ),
    ).toBe(true);
    expect(
      canAccessSuperAdminDashboard(
        { enable_super_admin: false } as never,
        ["create_organization"],
      ),
    ).toBe(false);
    expect(
      canAccessSuperAdminDashboard(
        { enable_super_admin: true } as never,
        [],
      ),
    ).toBe(false);
    expect(canAccessSuperAdminDashboard(undefined, ["create_organization"])).toBe(
      false,
    );
  });
});
