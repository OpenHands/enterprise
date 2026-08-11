import type { NavigateFunction, NavigateOptions } from "react-router";

const CROSS_APP_PATH_PREFIXES = [
  "/automations",
  "/canvas",
  "/integrations-hub",
] as const;

/**
 * Rewrite legacy `/automations` routes to the current `/canvas/automations`
 * path. The automations frontend moved under `/canvas`, so old deep links,
 * OAuth states, and `returnTo` values that still point at `/automations`
 * must be redirected to the new location. Paths that already start with
 * `/canvas` (including `/canvas/automations`) are returned unchanged.
 */
export function normalizeLegacyAutomationsPath(destination: string): string {
  if (!destination.startsWith("/automations")) {
    return destination;
  }
  try {
    const parsed = new URL(destination, window.location.origin);
    if (
      parsed.pathname === "/automations" ||
      parsed.pathname.startsWith("/automations/")
    ) {
      parsed.pathname = `/canvas/automations${parsed.pathname.slice("/automations".length)}`;
      return parsed.pathname + parsed.search + parsed.hash;
    }
  } catch {
    // Fall through; a non-parseable destination is left as-is.
  }
  return destination;
}

export function isCrossAppPath(destination: string): boolean {
  if (!destination.startsWith("/") || destination.startsWith("//")) {
    return false;
  }

  try {
    const { pathname } = new URL(destination, window.location.origin);
    return CROSS_APP_PATH_PREFIXES.some(
      (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
    );
  } catch {
    return false;
  }
}

export function navigateOrHardRedirect(
  navigate: NavigateFunction,
  destination: string,
  options?: NavigateOptions,
) {
  if (isCrossAppPath(destination)) {
    window.location.replace(normalizeLegacyAutomationsPath(destination));
    return;
  }

  navigate(destination, options);
}
