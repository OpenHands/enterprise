import { redirect } from "react-router";
import { queryClient } from "#/query-client-config";
import OptionService from "#/api/option-service/option-service.api";
import { WebClientConfig } from "#/api/option-service/option.types";
import { QUERY_KEYS, CONFIG_CACHE_OPTIONS } from "#/hooks/query/query-keys";
import { getFirstAvailablePath } from "#/utils/settings-utils";
import { hasPendingOrgSwitch } from "./org-url-param";
import { getActiveOrganizationUser } from "./permission-checks";
import { PermissionKey, rolePermissions } from "./permissions";

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
 * Redirects to the first available settings page if permission is denied.
 *
 * In OSS mode, permission checks are bypassed since there are no user roles.
 *
 * @param requiredPermission - The permission key to check
 * @param customRedirectPath - Optional custom path to redirect to (will still respect feature flags if not provided)
 * @returns A clientLoader function that can be exported from route files
 */
export const createPermissionGuard =
  (requiredPermission: PermissionKey, customRedirectPath?: string) =>
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
      return redirect(redirectPath);
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
