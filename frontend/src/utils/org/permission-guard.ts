import { replace } from "react-router";
import { queryClient } from "#/query-client-config";
import OptionService from "#/api/option-service/option-service.api";
import { WebClientConfig } from "#/api/option-service/option.types";
import { adminService } from "#/api/admin-service/admin-service.api";
import { QUERY_KEYS, CONFIG_CACHE_OPTIONS } from "#/hooks/query/query-keys";
import { getFirstAvailablePath } from "#/utils/settings-utils";
import { hasPendingOrgSwitch } from "./org-url-param";
import { getActiveOrganizationUser } from "./permission-checks";
import { PermissionKey, rolePermissions } from "./permissions";

/**
 * Whether the current user holds the instance-level super-admin role.
 * Shares the ``useIsSuperAdmin`` query cache (same query key), so this
 * loader-time check and the page's own render-time check only ever make
 * one request.
 */
export async function getIsSuperAdmin(): Promise<boolean> {
  try {
    return await queryClient.fetchQuery({
      queryKey: ["admin", "super-admin-status"],
      queryFn: adminService.getMySuperAdminStatus,
      staleTime: 1000 * 60 * 5,
    });
  } catch {
    return false;
  }
}

/**
 * Helper to get config, using fetchQuery for automatic caching and deduplication.
 * Uses the shared query key from QUERY_KEYS to ensure consistency across the app.
 */
async function getConfig(): Promise<WebClientConfig | undefined> {
  return queryClient.fetchQuery({
    queryKey: QUERY_KEYS.WEB_CLIENT_CONFIG,
    queryFn: OptionService.getConfig,
    ...CONFIG_CACHE_OPTIONS,
  });
}

/**
 * Super-admin bypass for the org-members page, scoped to the admin-issued
 * sign-up link feature (``enable_integrated_idp``). Without it there's no
 * new capability for a super admin to reach the page for -- no sign-up
 * link minting, no instance-wide "All Users" view -- so access must fall
 * back to the plain org-permission check, same as on main before that
 * feature existed.
 */
export async function getIsSuperAdminWithIntegratedIdp(): Promise<boolean> {
  const config = await getConfig();
  if (!config?.feature_flags?.enable_integrated_idp) {
    return false;
  }
  return getIsSuperAdmin();
}

/**
 * Gets the appropriate fallback path for permission denied scenarios.
 * Respects feature flags to avoid redirecting to hidden pages.
 */
async function getPermissionDeniedFallback(): Promise<string> {
  const config = await getConfig();

  const isSaas = config?.app_mode === "saas";
  const featureFlags = config?.feature_flags;

  // Get first available path that respects feature flags
  const fallbackPath = getFirstAvailablePath(isSaas, featureFlags);
  return fallbackPath ?? "/settings";
}

/**
 * Empty loader data for successful permission checks.
 *
 * React Router's dataStrategy treats a missing route result as an error
 * ("No result returned from dataStrategy for route …"). Returning `null`
 * can also look like missing loader data during revalidation/HMR, so always
 * return a concrete object on the allow path.
 */
const PERMISSION_GRANTED = {} as const;

/**
 * Creates a clientLoader guard that checks if the user has the required permission.
 * Redirects (replacing the history entry) to the first available settings
 * page if permission is denied.
 *
 * In OSS mode, permission checks are bypassed since there are no user roles.
 *
 * @param requiredPermission - The permission key to check
 * @param customRedirectPath - Optional custom path to redirect to (will still respect feature flags if not provided)
 * @param extraBypassCheck - Optional async check run after the OSS short-circuit;
 *   resolving `true` grants access without consulting the org-scoped permission
 *   below. Used by the org-members page so instance-level super admins (see
 *   ``server.routes.super_admins``) can reach it regardless of org membership.
 * @returns A clientLoader function that can be exported from route files
 */
export const createPermissionGuard =
  (
    requiredPermission: PermissionKey,
    customRedirectPath?: string,
    extraBypassCheck?: () => Promise<boolean>,
  ) =>
  async ({ request }: { request: Request }) => {
    // The settings loader is consuming a pending `?org=` switch on this pass
    // and will redirect without the param; redirecting here would drop it.
    if (hasPendingOrgSwitch(request)) return PERMISSION_GRANTED;

    // Get config to check app_mode. A failed config fetch (e.g. mock mode
    // proxying to a down backend) must not throw into ErrorBoundary.
    let config: WebClientConfig | undefined;
    try {
      config = await getConfig();
    } catch {
      return PERMISSION_GRANTED;
    }

    // In OSS mode, skip permission checks - all settings are accessible
    if (config?.app_mode === "oss") {
      return PERMISSION_GRANTED;
    }

    if (extraBypassCheck && (await extraBypassCheck())) {
      return PERMISSION_GRANTED;
    }

    const user = await getActiveOrganizationUser();

    const url = new URL(request.url);
    const currentPath = url.pathname;

    // Helper to get redirect response, avoiding infinite loops
    const getRedirectResponse = async () => {
      const redirectPath =
        customRedirectPath ?? (await getPermissionDeniedFallback());
      // Don't redirect to the same path to avoid infinite loops
      if (redirectPath === currentPath) {
        return PERMISSION_GRANTED;
      }
      return replace(redirectPath);
    };

    if (!user) {
      return getRedirectResponse();
    }

    const userRole = user.role ?? "member";

    if (!rolePermissions[userRole].includes(requiredPermission)) {
      return getRedirectResponse();
    }

    return PERMISSION_GRANTED;
  };
