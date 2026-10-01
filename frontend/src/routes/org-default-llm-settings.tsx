import { SuperAdminGroupSetupModal } from "#/components/features/super-admin/super-admin-group-setup-modal";
import { createPermissionGuard } from "#/utils/org/permission-guard";
import { LlmSettingsScreen } from "./llm-settings";

export const clientLoader = createPermissionGuard("view_llm_settings");

function OrgDefaultLlmSettingsScreen() {
  return (
    <>
      <LlmSettingsScreen scope="org" />
      <SuperAdminGroupSetupModal />
    </>
  );
}

export default OrgDefaultLlmSettingsScreen;
