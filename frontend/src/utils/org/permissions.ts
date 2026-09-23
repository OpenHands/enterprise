import { OrganizationUserRole } from "#/types/org";

/* PERMISSION TYPES */
type UserRoleChangePermissionKey = `change_user_role:${OrganizationUserRole}`;
type InviteUserToOrganizationKey = "invite_user_to_organization";

type ChangeOrganizationNamePermission = "change_organization_name";
type DeleteOrganizationPermission = "delete_organization";
type AddCreditsPermission = "add_credits";
type ViewBillingPermission = "view_billing";

type ManageSecretsPermission = "manage_secrets";
type ManageMCPPermission = "manage_mcp";
type ManageIntegrationsPermission = "manage_integrations";
type ManageIntegrationProvidersPermission = "manage_integration_providers";
type ManageApplicationSettingsPermission = "manage_application_settings";
type ManageAPIKeysPermission = "manage_api_keys";

type ViewLLMSettingsPermission = "view_llm_settings";
type EditLLMSettingsPermission = "edit_llm_settings";

type ManageOrgClaimsPermission = "manage_org_claims";
type CreateOrganizationPermission = "create_organization";
type ProvisionUserPermission = "provision_user";
type ManageSuperAdminsPermission = "manage_super_admins";

// Union of all permission keys
export type PermissionKey =
  | UserRoleChangePermissionKey
  | InviteUserToOrganizationKey
  | ChangeOrganizationNamePermission
  | DeleteOrganizationPermission
  | AddCreditsPermission
  | ViewBillingPermission
  | ManageSecretsPermission
  | ManageMCPPermission
  | ManageIntegrationsPermission
  | ManageIntegrationProvidersPermission
  | ManageApplicationSettingsPermission
  | ManageAPIKeysPermission
  | ViewLLMSettingsPermission
  | EditLLMSettingsPermission
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

/* PERMISSION ARRAYS */
const memberPerms: PermissionKey[] = [
  "manage_secrets",
  "manage_mcp",
  "manage_integrations",
  "manage_application_settings",
  "manage_api_keys",
  "view_llm_settings",
];

const adminOnly: PermissionKey[] = [
  "manage_integration_providers",
  "edit_llm_settings",
  "view_billing",
  "add_credits",
  "invite_user_to_organization",
  "change_user_role:member",
  "change_user_role:admin",
  "manage_org_claims",
];

const ownerOnly: PermissionKey[] = [
  "change_organization_name",
  "delete_organization",
  "change_user_role:owner",
];

const adminPerms: PermissionKey[] = [...memberPerms, ...adminOnly];
const ownerPerms: PermissionKey[] = [...adminPerms, ...ownerOnly];

export const rolePermissions: Record<OrganizationUserRole, PermissionKey[]> = {
  owner: ownerPerms,
  admin: adminPerms,
  member: memberPerms,
};
