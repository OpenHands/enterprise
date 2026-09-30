import { useQueries } from "@tanstack/react-query";
import { useMemo } from "react";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSuperAdminOrganizations } from "#/hooks/query/use-super-admin";
import {
  mergeSuperAdminSnapshots,
  snapshotFromLiveOrgUsage,
  type SuperAdminUsageView,
} from "#/components/features/super-admin/super-admin-usage-data";

const MAX_ORGS_FOR_AGGREGATE = 12;

export function useSuperAdminUsage({
  selectedOrgIds,
  timeWindow,
}: {
  selectedOrgIds: string[];
  timeWindow: string;
}): {
  orgs: { id: string; name: string }[];
  usage: SuperAdminUsageView;
  isLoading: boolean;
  isError: boolean;
} {
  const {
    data: adminOrgs,
    isLoading: orgsLoading,
    isError: orgsError,
  } = useSuperAdminOrganizations();

  const orgs = useMemo(
    () =>
      (adminOrgs ?? [])
        .filter((org) => !org.is_personal)
        .map((org) => ({ id: org.id, name: org.name })),
    [adminOrgs],
  );

  const targetOrgs = useMemo(() => {
    if (selectedOrgIds.length > 0) {
      return orgs.filter((org) => selectedOrgIds.includes(org.id));
    }
    return orgs.slice(0, MAX_ORGS_FOR_AGGREGATE);
  }, [orgs, selectedOrgIds]);

  const queries = useQueries({
    queries: targetOrgs.map((org) => ({
      queryKey: ["super-admin", "usage", org.id, timeWindow] as const,
      queryFn: async () => {
        const [stats, userUsage] = await Promise.all([
          organizationService.getUsageStats({
            orgId: org.id,
            timeWindow,
          }),
          organizationService.getUserUsageStats({
            orgId: org.id,
            limit: 50,
            offset: 0,
          }),
        ]);
        return snapshotFromLiveOrgUsage({
          orgId: org.id,
          orgName: org.name,
          stats,
          users: userUsage.items.map((user) => ({
            ...user,
            org_name: org.name,
          })),
        });
      },
      enabled: !!org.id,
    })),
  });

  const isLoading = orgsLoading || queries.some((query) => query.isLoading);
  const isError = orgsError || queries.some((query) => query.isError);
  const dataUpdatedAt = queries.map((query) => query.dataUpdatedAt).join(",");

  const usage = useMemo(() => {
    const snapshots = queries
      .map((query) => query.data)
      .filter(
        (snapshot): snapshot is NonNullable<typeof snapshot> => !!snapshot,
      );
    return mergeSuperAdminSnapshots(selectedOrgIds, snapshots);
    // dataUpdatedAt tracks when any query result changes without depending on
    // the unstable queries array identity from useQueries.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see above
  }, [dataUpdatedAt, selectedOrgIds]);

  return { orgs, usage, isLoading, isError };
}
