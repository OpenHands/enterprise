import { AgentProfilesView } from "#/components/features/settings/agent-profiles/agent-profiles-view";
import { createPermissionGuard } from "#/utils/org/permission-guard";

export const clientLoader = createPermissionGuard("view_llm_settings");

/** Personal "Agent profiles" page (SaaS): the member's own profile library. */
export default function AgentProfilesSettingsScreen() {
  return <AgentProfilesView />;
}
