import { useConfig } from "#/hooks/query/use-config";
import { usePermission } from "#/hooks/organizations/use-permissions";

/**
 * Whether the user may add, edit and delete the organization's shared agent
 * profiles. The backend guards org-scope agent profile writes with
 * `edit_org_settings`, so the UI reads that permission from `/me`.
 */
export const useCanManageOrgAgentProfiles = (): boolean => {
  const { data: config } = useConfig();
  const { hasPermission } = usePermission();
  return (
    !!config?.feature_flags?.enable_org_agent_profiles &&
    hasPermission("edit_org_settings")
  );
};
