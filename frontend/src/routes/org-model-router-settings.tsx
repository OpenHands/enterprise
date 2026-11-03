import { ModelRouterManager } from "#/components/features/settings/model-router/model-router-manager";
import { usePermission } from "#/hooks/organizations/use-permissions";
import { useOrgTypeAndAccess } from "#/hooks/use-org-type-and-access";
import { createPermissionGuard } from "#/utils/org/permission-guard";

export const clientLoader = createPermissionGuard("view_llm_settings");

/**
 * Org Model Router page (OHE-3508). Every member can read the routers; only
 * users who may edit the org LLM settings manage them.
 */
function OrgModelRouterSettingsScreen() {
  const { organizationId } = useOrgTypeAndAccess();
  const { hasPermission } = usePermission();

  if (!organizationId) {
    return null;
  }

  return (
    <ModelRouterManager
      orgId={organizationId}
      canManage={hasPermission("edit_llm_settings")}
    />
  );
}

export default OrgModelRouterSettingsScreen;
