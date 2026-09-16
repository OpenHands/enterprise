export const PERSONAL_INTEGRATIONS_PATHS = {
  integrations: "/settings/integrations",
  agentRequests: "/settings/integrations/agent-requests",
  agentConnection: "/settings/integrations/agent-connection",
} as const;

export const INTEGRATIONS_HUB_PATHS = {
  root: "/settings/integrations-hub",
  adminOverview: "/settings/integrations-hub/admin-overview",
  adminCatalog: "/settings/integrations-hub/admin-catalog",
  adminUserRequests: "/settings/integrations-hub/admin-user-requests",
} as const;

export const LEGACY_HUB_PERSONAL_REDIRECTS: Record<string, string> = {
  "/settings/integrations-hub/agent-requests":
    PERSONAL_INTEGRATIONS_PATHS.agentRequests,
  "/settings/integrations-hub/agent-connection":
    PERSONAL_INTEGRATIONS_PATHS.agentConnection,
};

export const INTEGRATIONS_HUB_ADMIN_PATHS = [
  INTEGRATIONS_HUB_PATHS.adminOverview,
  INTEGRATIONS_HUB_PATHS.adminCatalog,
  INTEGRATIONS_HUB_PATHS.adminUserRequests,
] as const;

export function isPersonalIntegrationsPath(pathname: string): boolean {
  return (
    pathname === PERSONAL_INTEGRATIONS_PATHS.integrations ||
    pathname.startsWith(`${PERSONAL_INTEGRATIONS_PATHS.integrations}/`)
  );
}

export function isIntegrationsHubPath(pathname: string): boolean {
  return (
    pathname === INTEGRATIONS_HUB_PATHS.root ||
    pathname.startsWith(`${INTEGRATIONS_HUB_PATHS.root}/`)
  );
}

export function isIntegrationsHubAdminPath(pathname: string): boolean {
  return INTEGRATIONS_HUB_ADMIN_PATHS.some((path) => pathname === path);
}
