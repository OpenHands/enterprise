import { useState } from "react";
import { useMe } from "#/hooks/query/use-me";
import { useSuperAdminUser } from "#/hooks/query/use-super-admin";
import { superAdminCanViewOrg } from "./super-admin-org-access";
import { useSuperAdminOpenOrg } from "./use-super-admin-open-org";

/**
 * Opens an organization when the signed-in Super Admin is an active member.
 * Otherwise it holds the target so the page can ask them to grant access.
 */
export function useSuperAdminViewOrg() {
  const openOrg = useSuperAdminOpenOrg();
  const meQuery = useMe();
  const selfQuery = useSuperAdminUser(meQuery.data?.user_id);
  const [pendingOrg, setPendingOrg] = useState<{
    orgId: string;
    orgName: string;
  } | null>(null);

  const viewOrg = (orgId: string, orgName: string) => {
    if (!selfQuery.isSuccess || meQuery.isLoading) {
      return;
    }
    if (superAdminCanViewOrg(selfQuery.data.memberships, orgId)) {
      openOrg(orgId, orgName);
      return;
    }
    setPendingOrg({ orgId, orgName });
  };

  // A personal workspace shares its owner's user id, so only its owner may
  // open it. Super Admins cannot join another user's personal workspace.
  const canViewOrg = (orgId: string, isPersonal: boolean) =>
    !isPersonal || orgId === meQuery.data?.user_id;

  const dismissGrant = () => setPendingOrg(null);

  const confirmGrant = () => {
    if (!pendingOrg) {
      return;
    }
    const target = pendingOrg;
    setPendingOrg(null);
    openOrg(target.orgId, target.orgName);
  };

  return {
    viewOrg,
    canViewOrg,
    pendingOrg,
    dismissGrant,
    confirmGrant,
    userId: meQuery.data?.user_id ?? null,
  };
}
