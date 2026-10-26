import { OrganizationUserRole } from "#/types/org";

/* PERMISSION TYPES */
type UserRoleChangePermissionKey = `change_user_role:${OrganizationUserRole}`;
type InviteUserToOrganizationKey = "invite_user_to_organization";

type ChangeOrganizationNamePermission = "change_organization_name";
type DeleteOrganizationPermission = "delete_organization";
type AddCreditsPermission = "add_credits";
type ViewBillingPermission = "view_billing";

type ManageSecretsPermission = "manage_secrets";
type ManageOrgSecretsPermission = "manage_org_secrets";
type ManageOrgAgentProfilesPermission = "manage_org_agent_profiles";
type ManageMCPPermission = "manage_mcp";
type ManageIntegrationsPermission = "manage_integrations";
type ManageIntegrationProvidersPermission = "manage_integration_providers";
type ManageApplicationSettingsPermission = "manage_application_settings";
type ManageAPIKeysPermission = "manage_api_keys";

type ViewLLMSettingsPermission = "view_llm_settings";
type EditLLMSettingsPermission = "edit_llm_settings";

type EditOrgSettingsPermission = "edit_org_settings";
type ViewOrgConversationsPermission = "view_org_conversations";

type ManageOrgClaimsPermission = "manage_org_claims";
type CreateOrganizationPermission = "create_organization";
type ProvisionUserPermission = "provision_user";
type ManageSuperAdminsPermission = "manage_super_admins";

/**
 * Permission keys the frontend checks. Values match the backend
 * ``Permission`` enum (``server/auth/authorization.py``). The backend computes
 * each user's permissions and returns them in ``/me`` (``permissions``); the
 * frontend reads that list and keeps no role-to-permission map.
 */
export type PermissionKey =
  | UserRoleChangePermissionKey
  | InviteUserToOrganizationKey
  | ChangeOrganizationNamePermission
  | DeleteOrganizationPermission
  | AddCreditsPermission
  | ViewBillingPermission
  | ManageSecretsPermission
  | ManageOrgSecretsPermission
  | ManageOrgAgentProfilesPermission
  | ManageMCPPermission
  | ManageIntegrationsPermission
  | ManageIntegrationProvidersPermission
  | ManageApplicationSettingsPermission
  | ManageAPIKeysPermission
  | ViewLLMSettingsPermission
  | EditLLMSettingsPermission
  | EditOrgSettingsPermission
  | ViewOrgConversationsPermission
  | ManageOrgClaimsPermission
  | CreateOrganizationPermission
  | ProvisionUserPermission
  | ManageSuperAdminsPermission;

/** Instance-level permissions that identify a Super Admin in the FE.

 *  Must stay aligned with backend ``is_instance_super_admin`` /
 *  ``MANAGE_SUPER_ADMINS``. Do **not** include ``provision_user`` — that
 *  permission is also granted to org owners/admins.
 */
export const INSTANCE_SUPER_ADMIN_PERMISSIONS: PermissionKey[] = [
  "manage_super_admins",
];

export function isInstanceSuperAdmin(
  permissions?: readonly string[] | null,
): boolean {
  return INSTANCE_SUPER_ADMIN_PERMISSIONS.some(
    (permission) => permissions?.includes(permission) === true,
  );
}
