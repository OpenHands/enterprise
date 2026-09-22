import { Navigate, Outlet, useLocation } from "react-router";
import {
  IntegrationsHubProvider,
  useIntegrationsHub,
} from "#/hooks/query/use-integrations-hub";
import {
  INTEGRATIONS_HUB_PATHS,
  LEGACY_HUB_PERSONAL_REDIRECTS,
  PERSONAL_INTEGRATIONS_PATHS,
} from "#/components/features/integrations-hub/integrations-hub-paths";
import { IntegrationsHubNav } from "#/components/features/integrations-hub/integrations-hub-nav";

export const handle = { hideTitle: true, wideContent: true };

// Production cutover reconnect modal lives on PersonalIntegrationsLayout
// (first visit when enable_integrations_hub is on). Admin Hub routes
// redirect personal workspaces to that surface.

function IntegrationsHubLayoutInner() {
  const location = useLocation();
  const hub = useIntegrationsHub();
  const legacyPersonalPath = LEGACY_HUB_PERSONAL_REDIRECTS[location.pathname];

  if (legacyPersonalPath) {
    return <Navigate to={legacyPersonalPath} replace />;
  }

  if (location.pathname === INTEGRATIONS_HUB_PATHS.root) {
    return hub.isPersonalWorkspace ? (
      <Navigate to={PERSONAL_INTEGRATIONS_PATHS.integrations} replace />
    ) : (
      <Navigate to={INTEGRATIONS_HUB_PATHS.adminOverview} replace />
    );
  }

  if (hub.isPersonalWorkspace) {
    return <Navigate to={PERSONAL_INTEGRATIONS_PATHS.integrations} replace />;
  }

  return (
    <div
      className="flex min-h-0 flex-col gap-4 md:flex-row md:gap-6 lg:gap-10"
      data-testid="integrations-hub-layout"
    >
      <IntegrationsHubNav
        variant="admin"
        userRequestCount={hub.userRequests.length}
      />
      <div className="mx-auto flex w-full min-w-0 max-w-[800px] flex-1 flex-col gap-6">
        <Outlet />
      </div>
    </div>
  );
}

export function IntegrationsHubLayout() {
  return (
    <IntegrationsHubProvider>
      <IntegrationsHubLayoutInner />
    </IntegrationsHubProvider>
  );
}
