import { useState } from "react";
import { useMe } from "#/hooks/query/use-me";
import { useSuperAdminUsers } from "#/hooks/query/use-super-admin";
import { superAdminCanViewOrg } from "./super-admin-org-access";
import { useSuperAdminOpenOrg } from "./use-super-admin-open-org";

/**
 * Opens an organization when the signed-in Super Admin is an active member.
 * Otherwise it holds the target so the page can ask them to grant access.
 */
export function useSuperAdminViewOrg() {
  const openOrg = useSuperAdminOpenOrg();
  const meQuery = useMe();
  const usersQuery = useSuperAdminUsers();
  const [pendingOrg, setPendingOrg] = useState<{
    orgId: string;
    orgName: string;
  } | null>(null);

  const viewOrg = (orgId: string, orgName: string) => {
    if (!usersQuery.isSuccess || meQuery.isLoading) {
      return;
    }
    const self = usersQuery.data.find(
      (user) => user.user_id === meQuery.data?.user_id,
    );
    if (superAdminCanViewOrg(self?.memberships, orgId)) {
      openOrg(orgId, orgName);
      return;
    }
    setPendingOrg({ orgId, orgName });
  };

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
    pendingOrg,
    dismissGrant,
    confirmGrant,
    userId: meQuery.data?.user_id ?? null,
  };
}
