import { Navigate } from "react-router";
import { INTEGRATIONS_HUB_PATHS } from "#/components/features/integrations-hub/integrations-hub-paths";

export default function IntegrationsHubIndexRedirect() {
  return <Navigate to={INTEGRATIONS_HUB_PATHS.adminOverview} replace />;
}
