import {
  handle,
  PersonalIntegrationsLayout,
} from "#/components/features/integrations-hub/personal-integrations-layout";
import { createPermissionGuard } from "#/utils/org/permission-guard";

export const clientLoader = createPermissionGuard("manage_integrations");

export { handle };

export default PersonalIntegrationsLayout;
