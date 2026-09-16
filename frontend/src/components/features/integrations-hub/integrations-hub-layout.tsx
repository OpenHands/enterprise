import { Navigate, Outlet, useLocation } from "react-router";
import {
  INTEGRATIONS_HUB_PATHS,
  LEGACY_HUB_PERSONAL_REDIRECTS,
  PERSONAL_INTEGRATIONS_PATHS,
} from "#/components/features/integrations-hub/integrations-hub-paths";
import { IntegrationsHubNav } from "#/components/features/integrations-hub/integrations-hub-nav";
import {
  IntegrationsHubStubProvider,
  useIntegrationsHubStub,
} from "#/hooks/query/use-integrations-hub-stub";

export const handle = { hideTitle: true, wideContent: true };

// CUTOVER: Moving users from Settings > Integrations onto Hub-backed
// personal Integrations will break existing connections (git tokens,
// Slack, Jira/Linear, and other provider credentials stored on the old
// page).
//
// Before Hub-backed Integrations is the only personal integrations
// surface, add a first-visit modal here (or on PersonalIntegrationsLayout)
// that:
//   1. Lists which existing integrations broke or need reconnecting
//   2. Explains how to fix each one in Integrations
// Do not treat enable_integrations_hub as a production cutover until
// that modal ships.

function IntegrationsHubLayoutInner() {
  const location = useLocation();
  const hub = useIntegrationsHubStub();
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

  const isVariantsPreview =
    location.pathname === INTEGRATIONS_HUB_PATHS.connectorModalVariants;

  return (
    <div
      className="flex min-h-0 flex-col gap-4 md:flex-row md:gap-6 lg:gap-10"
      data-testid="integrations-hub-layout"
    >
      {isVariantsPreview ? null : (
        <IntegrationsHubNav
          variant="admin"
          userRequestCount={hub.userRequests.length}
        />
      )}
      <div
        className={
          isVariantsPreview
            ? "mx-auto flex w-full min-w-0 flex-1 flex-col gap-6"
            : "mx-auto flex w-full min-w-0 max-w-[800px] flex-1 flex-col gap-6"
        }
      >
        <Outlet />
      </div>
    </div>
  );
}

export function IntegrationsHubLayout() {
  return (
    <IntegrationsHubStubProvider>
      <IntegrationsHubLayoutInner />
    </IntegrationsHubStubProvider>
  );
}
