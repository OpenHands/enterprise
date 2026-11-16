import type { SuperAdminApiMembership } from "#/api/super-admin-service/super-admin-service.api";

/**
 * A Super Admin can open an organization only when they already have an
 * active membership. Suspended and missing rows both stay closed until
 * they grant themselves access.
 */
export function superAdminCanViewOrg(
  memberships: Pick<SuperAdminApiMembership, "org_id" | "status">[] | undefined,
  orgId: string,
): boolean {
  return (
    memberships?.some(
      (membership) =>
        membership.org_id === orgId && membership.status === "active",
    ) ?? false
  );
}
