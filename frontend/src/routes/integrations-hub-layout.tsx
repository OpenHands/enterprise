import {
  handle,
  IntegrationsHubLayout,
} from "#/components/features/integrations-hub/integrations-hub-layout";
import { createPermissionGuard } from "#/utils/org/permission-guard";

export const clientLoader = createPermissionGuard("manage_integrations");

export { handle };

export default IntegrationsHubLayout;
