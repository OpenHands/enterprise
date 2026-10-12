import type {
  ConversationRow,
  UserUsageRow,
} from "#/components/features/admin-dashboard/usage-dashboard-tabs";

export interface SuperAdminOrgUsageSnapshot {
  orgId: string;
  orgName: string;
  conversations: number;
  activeConversations: number;
  spend: number;
  dailyUsage: { date: string; conversations: number }[];
  agentUsage: { agent_name: string; total_cost: number }[];
  modelUsage: {
    model_name: string;
    conversation_count: number;
    total_tokens: number;
    total_cost: number;
  }[];
  users: Array<UserUsageRow & { org_name: string }>;
  conversationRows: Array<ConversationRow & { org_name: string }>;
}

export interface SuperAdminUsageView {
  selectedOrgIds: string[];
  snapshots: SuperAdminOrgUsageSnapshot[];
  conversations: number;
  activeConversations: number;
  spend: number;
  dailyUsage: { date: string; conversations: number }[];
  agentUsage: { agent_name: string; total_cost: number }[];
  modelUsage: SuperAdminOrgUsageSnapshot["modelUsage"];
  users: SuperAdminOrgUsageSnapshot["users"];
  conversationRows: SuperAdminOrgUsageSnapshot["conversationRows"];
}

function mergeDaily(
  snapshots: SuperAdminOrgUsageSnapshot[],
): SuperAdminOrgUsageSnapshot["dailyUsage"] {
  const byDate = new Map<string, number>();
  snapshots.forEach((snapshot) => {
    snapshot.dailyUsage.forEach((point) => {
      byDate.set(
        point.date,
        (byDate.get(point.date) ?? 0) + point.conversations,
      );
    });
  });
  return [...byDate.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([date, conversations]) => ({ date, conversations }));
}

function mergeAgents(snapshots: SuperAdminOrgUsageSnapshot[]) {
  const byName = new Map<string, number>();
  snapshots.forEach((snapshot) => {
    snapshot.agentUsage.forEach((row) => {
      byName.set(
        row.agent_name,
        (byName.get(row.agent_name) ?? 0) + row.total_cost,
      );
    });
  });
  return [...byName.entries()]
    .map(([agent_name, total_cost]) => ({
      agent_name,
      total_cost: Number(total_cost.toFixed(2)),
    }))
    .sort((left, right) => right.total_cost - left.total_cost);
}

function mergeModels(snapshots: SuperAdminOrgUsageSnapshot[]) {
  const byName = new Map<
    string,
    SuperAdminOrgUsageSnapshot["modelUsage"][number]
  >();
  snapshots.forEach((snapshot) => {
    snapshot.modelUsage.forEach((row) => {
      const current = byName.get(row.model_name);
      if (!current) {
        byName.set(row.model_name, { ...row });
        return;
      }
      byName.set(row.model_name, {
        model_name: row.model_name,
        conversation_count: current.conversation_count + row.conversation_count,
        total_tokens: current.total_tokens + row.total_tokens,
        total_cost: Number((current.total_cost + row.total_cost).toFixed(2)),
      });
    });
  });
  return [...byName.values()].sort(
    (left, right) => right.total_cost - left.total_cost,
  );
}

export function mergeSuperAdminSnapshots(
  selectedOrgIds: string[],
  snapshots: SuperAdminOrgUsageSnapshot[],
): SuperAdminUsageView {
  return {
    selectedOrgIds,
    snapshots,
    conversations: snapshots.reduce((sum, row) => sum + row.conversations, 0),
    activeConversations: snapshots.reduce(
      (sum, row) => sum + row.activeConversations,
      0,
    ),
    spend: Number(
      snapshots.reduce((sum, row) => sum + row.spend, 0).toFixed(2),
    ),
    dailyUsage: mergeDaily(snapshots),
    agentUsage: mergeAgents(snapshots),
    modelUsage: mergeModels(snapshots),
    users: snapshots.flatMap((row) => row.users),
    conversationRows: snapshots.flatMap((row) => row.conversationRows),
  };
}

export interface LiveOrgUsageStatsInput {
  orgId: string;
  orgName: string;
  stats: {
    usage_conversation_count: number;
    agent_runs: number;
    estimated_spend: number;
    daily_usage: { date: string; conversations: number }[];
    model_usage: {
      model_name: string;
      conversation_count: number;
      total_tokens: number;
      total_cost: number;
    }[];
    agent_usage: { agent_name: string; total_cost: number }[];
  };
  users?: Array<UserUsageRow & { org_name?: string }>;
  conversationRows?: Array<ConversationRow & { org_name?: string }>;
}

export function snapshotFromLiveOrgUsage(
  input: LiveOrgUsageStatsInput,
): SuperAdminOrgUsageSnapshot {
  return {
    orgId: input.orgId,
    orgName: input.orgName,
    conversations: input.stats.usage_conversation_count,
    activeConversations: input.stats.agent_runs,
    spend: input.stats.estimated_spend,
    dailyUsage: input.stats.daily_usage.map((point) => ({
      date: point.date,
      conversations: point.conversations,
    })),
    agentUsage: input.stats.agent_usage.map((row) => ({
      agent_name: row.agent_name,
      total_cost: row.total_cost,
    })),
    modelUsage: input.stats.model_usage.map((row) => ({
      model_name: row.model_name,
      conversation_count: row.conversation_count,
      total_tokens: row.total_tokens,
      total_cost: row.total_cost,
    })),
    users: (input.users ?? []).map((user) => ({
      ...user,
      org_name: user.org_name ?? input.orgName,
    })),
    conversationRows: (input.conversationRows ?? []).map((row) => ({
      ...row,
      org_name: row.org_name ?? input.orgName,
    })),
  };
}
