import { useQueries, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSuperAdminOrganizations } from "#/hooks/query/use-super-admin";
import {
  mergeSuperAdminSnapshots,
  snapshotFromLiveOrgUsage,
  type SuperAdminUsageView,
} from "#/components/features/super-admin/super-admin-usage-data";
import type { ConversationRow } from "#/components/features/admin-dashboard/usage-dashboard-tabs";

/** Cap “All orgs” fan-out so the dashboard stays responsive. */
const MAX_ORGS_FOR_AGGREGATE = 12;
/** Per-org conversation pull for client-side merge/filter/sort. */
const CONVERSATIONS_PER_ORG = 100;

function mapOrgConversation(
  item: {
    id: string;
    title: string;
    user_email: string | null;
    total_tokens: number;
    accumulated_cost: number;
    created_at: string;
    updated_at: string;
    pr_number: number[];
    selected_repository: string | null;
    pr_merged: boolean | null;
    agent_kind: string;
    llm_model: string | null;
    trigger: string | null;
    execution_status: string | null;
    sandbox_status: string | null;
  },
  orgId: string,
  orgName: string,
): ConversationRow & { org_name: string; org_id: string } {
  return {
    id: item.id,
    user_email: item.user_email,
    org_name: orgName,
    org_id: orgId,
    total_tokens: item.total_tokens,
    accumulated_cost: item.accumulated_cost,
    created_at: item.created_at,
    updated_at: item.updated_at,
    pr_number: item.pr_number,
    selected_repository: item.selected_repository,
    pr_merged: item.pr_merged,
    agent_kind: item.agent_kind,
    llm_model: item.llm_model,
    trigger: item.trigger,
    execution_status: item.execution_status,
    sandbox_status: item.sandbox_status,
    title: item.title,
  };
}

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

  const conversationTimeWindow = timeWindow === "ytd" ? undefined : timeWindow;

  const queries = useQueries({
    queries: targetOrgs.map((org) => ({
      queryKey: [
        "super-admin",
        "usage",
        org.id,
        timeWindow,
        conversationTimeWindow,
      ] as const,
      queryFn: async () => {
        const [stats, userUsage, conversations] = await Promise.all([
          organizationService.getUsageStats({
            orgId: org.id,
            timeWindow,
          }),
          organizationService.getUserUsageStats({
            orgId: org.id,
            limit: 50,
            offset: 0,
          }),
          organizationService.getConversations({
            orgId: org.id,
            page: 1,
            perPage: CONVERSATIONS_PER_ORG,
            sortBy: "updated_at",
            sortOrder: "desc",
            timeWindow: conversationTimeWindow,
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
          conversationRows: conversations.items.map((item) =>
            mapOrgConversation(item, org.id, org.name),
          ),
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

export function useInvalidateSuperAdminUsage() {
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({
      queryKey: ["super-admin", "usage"],
    });
}
