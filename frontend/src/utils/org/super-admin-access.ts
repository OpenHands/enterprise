import type { WebClientFeatureFlags } from "#/api/option-service/option.types";
import { isInstanceSuperAdmin } from "#/utils/org/permissions";

/**
 * Super Admin dashboard is behind ENABLE_SUPER_ADMIN. The feature flag must
 * be on and the signed-in user must hold an instance Super Admin permission.
 */
export function canAccessSuperAdminDashboard(
  featureFlags: WebClientFeatureFlags | undefined,
  permissions?: readonly string[] | null,
): boolean {
  return (
    featureFlags?.enable_super_admin === true &&
    isInstanceSuperAdmin(permissions)
  );
}
