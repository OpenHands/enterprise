import type {
  ConversationRow,
  UserUsageRow,
} from "#/components/features/admin-dashboard/usage-dashboard-tabs";

export type SuperAdminUsageWindow = "7d" | "30d" | "90d" | "ytd";

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

const WINDOW_SCALE: Record<SuperAdminUsageWindow, number> = {
  "7d": 0.28,
  "30d": 1,
  "90d": 2.6,
  ytd: 7.1,
};

const WINDOW_DAYS: Record<SuperAdminUsageWindow, number> = {
  "7d": 7,
  "30d": 30,
  "90d": 90,
  ytd: 120,
};

const ANCHOR_DATE = "2026-09-18";

interface OrgUsageSeed {
  id: string;
  name: string;
  conversations: number;
  active: number;
  spend: number;
  agents: { agent_name: string; share: number }[];
  models: {
    model_name: string;
    conv: number;
    tokens: number;
    cost: number;
  }[];
  users: Array<UserUsageRow & { org_name?: string }>;
  conversationRows: ConversationRow[];
}

const ORG_USAGE_SEEDS: OrgUsageSeed[] = [
  {
    id: "2",
    name: "Acme Corp",
    conversations: 820,
    active: 14,
    spend: 6410,
    agents: [
      { agent_name: "OpenHands", share: 0.62 },
      { agent_name: "Claude", share: 0.28 },
      { agent_name: "Codex", share: 0.1 },
    ],
    models: [
      {
        model_name: "anthropic/claude-sonnet-4-5",
        conv: 480,
        tokens: 18_200_000,
        cost: 3900,
      },
      {
        model_name: "openai/gpt-5",
        conv: 220,
        tokens: 9_400_000,
        cost: 1680,
      },
      {
        model_name: "openhands/claude-sonnet-4",
        conv: 120,
        tokens: 4_100_000,
        cost: 830,
      },
    ],
    users: [
      {
        user_id: "u-1",
        user_name: "openhands",
        user_email: "me@acme.org",
        conversation_count: 186,
        first_conversation_at: "2026-01-14T16:20:00Z",
        last_conversation_at: "2026-09-17T21:04:00Z",
        first_login_at: "2026-01-12T09:00:00Z",
        last_login_at: "2026-09-18T08:12:00Z",
        spend_mtd: 1840,
        spend_ytd: 9200,
        spend_lifetime: 14820,
        budget_monthly_limit: 4000,
        prs_merged: 41,
      },
      {
        user_id: "u-2",
        user_name: "Alex Rivera",
        user_email: "alex@acme.org",
        conversation_count: 94,
        first_conversation_at: "2026-02-03T11:10:00Z",
        last_conversation_at: "2026-09-16T18:42:00Z",
        first_login_at: "2026-02-01T14:00:00Z",
        last_login_at: "2026-09-17T19:20:00Z",
        spend_mtd: 720,
        spend_ytd: 4100,
        spend_lifetime: 6100,
        budget_monthly_limit: 1500,
        prs_merged: 18,
      },
    ],
    conversationRows: [
      {
        id: "c-acme-1",
        user_email: "me@acme.org",
        title: "Harden billing webhook retries",
        total_tokens: 184220,
        accumulated_cost: 42.18,
        created_at: "2026-09-17T14:12:00Z",
        updated_at: "2026-09-18T07:40:00Z",
        pr_number: [1842],
        selected_repository: "acme/billing",
        pr_merged: false,
        agent_kind: "openhands",
        llm_model: "anthropic/claude-sonnet-4-5",
        trigger: "gui",
        execution_status: "running",
      },
      {
        id: "c-acme-2",
        user_email: "alex@acme.org",
        title: "Migrate invoices to v2 schema",
        total_tokens: 96210,
        accumulated_cost: 18.4,
        created_at: "2026-09-15T09:04:00Z",
        updated_at: "2026-09-16T16:22:00Z",
        pr_number: [1838],
        selected_repository: "acme/billing",
        pr_merged: true,
        agent_kind: "acp",
        llm_model: "anthropic/claude-sonnet-4-5",
        trigger: "suggested_task",
        execution_status: "finished",
      },
      {
        id: "c-acme-3",
        user_email: "me@acme.org",
        title: "Fix SSO group sync",
        total_tokens: 41200,
        accumulated_cost: 7.9,
        created_at: "2026-09-12T18:30:00Z",
        updated_at: "2026-09-12T20:05:00Z",
        agent_kind: "openhands",
        llm_model: "openai/gpt-5",
        trigger: "gui",
        execution_status: "idle",
      },
    ],
  },
  {
    id: "4",
    name: "All Hands AI",
    conversations: 410,
    active: 8,
    spend: 3900,
    agents: [
      { agent_name: "OpenHands", share: 0.74 },
      { agent_name: "Claude", share: 0.18 },
      { agent_name: "Codex", share: 0.08 },
    ],
    models: [
      {
        model_name: "anthropic/claude-sonnet-4-5",
        conv: 260,
        tokens: 11_400_000,
        cost: 2480,
      },
      {
        model_name: "openhands/claude-sonnet-4",
        conv: 150,
        tokens: 5_600_000,
        cost: 1420,
      },
    ],
    users: [
      {
        user_id: "u-3",
        user_name: "Jordan Lee",
        user_email: "jordan@all-hands.dev",
        conversation_count: 71,
        first_conversation_at: "2026-03-11T15:40:00Z",
        last_conversation_at: "2026-09-18T06:18:00Z",
        first_login_at: "2026-03-09T10:00:00Z",
        last_login_at: "2026-09-18T06:10:00Z",
        spend_mtd: 540,
        spend_ytd: 2860,
        spend_lifetime: 3340,
        budget_monthly_limit: 1200,
        prs_merged: 12,
      },
      {
        user_id: "u-5",
        user_name: "Riley Chen",
        user_email: "riley@all-hands.dev",
        conversation_count: 128,
        first_conversation_at: "2026-02-20T13:00:00Z",
        last_conversation_at: "2026-09-17T22:51:00Z",
        first_login_at: "2026-02-18T09:30:00Z",
        last_login_at: "2026-09-17T22:40:00Z",
        spend_mtd: 980,
        spend_ytd: 5120,
        spend_lifetime: 7400,
        budget_monthly_limit: 2500,
        prs_merged: 27,
      },
    ],
    conversationRows: [
      {
        id: "c-aha-1",
        user_email: "riley@all-hands.dev",
        title: "Ship settings left-nav IA",
        total_tokens: 221400,
        accumulated_cost: 51.2,
        created_at: "2026-09-16T11:08:00Z",
        updated_at: "2026-09-18T08:01:00Z",
        pr_number: [412],
        selected_repository: "OpenHands/OpenHands",
        pr_merged: false,
        agent_kind: "openhands",
        llm_model: "anthropic/claude-sonnet-4-5",
        trigger: "gui",
        execution_status: "running",
      },
      {
        id: "c-aha-2",
        user_email: "jordan@all-hands.dev",
        title: "Document org compare dashboard",
        total_tokens: 33400,
        accumulated_cost: 6.15,
        created_at: "2026-09-10T17:22:00Z",
        updated_at: "2026-09-11T09:40:00Z",
        agent_kind: "openhands",
        llm_model: "openhands/claude-sonnet-4",
        trigger: "gui",
        execution_status: "finished",
      },
    ],
  },
  {
    id: "3",
    name: "Beta LLC",
    conversations: 186,
    active: 3,
    spend: 1480,
    agents: [
      { agent_name: "OpenHands", share: 0.55 },
      { agent_name: "Codex", share: 0.45 },
    ],
    models: [
      {
        model_name: "openai/gpt-5",
        conv: 110,
        tokens: 4_800_000,
        cost: 910,
      },
      {
        model_name: "anthropic/claude-sonnet-4-5",
        conv: 76,
        tokens: 2_200_000,
        cost: 570,
      },
    ],
    users: [
      {
        user_id: "u-4",
        user_name: "Sam Patel",
        user_email: "sam@beta.llc",
        conversation_count: 22,
        first_conversation_at: "2026-04-02T12:18:00Z",
        last_conversation_at: "2026-09-14T15:33:00Z",
        first_login_at: "2026-03-28T16:00:00Z",
        last_login_at: "2026-09-14T15:10:00Z",
        spend_mtd: 210,
        spend_ytd: 980,
        spend_lifetime: 1120,
        budget_monthly_limit: 800,
        prs_merged: 3,
      },
    ],
    conversationRows: [
      {
        id: "c-beta-1",
        user_email: "sam@beta.llc",
        title: "Prototype inventory agent",
        total_tokens: 54800,
        accumulated_cost: 11.3,
        created_at: "2026-09-14T10:00:00Z",
        updated_at: "2026-09-14T15:33:00Z",
        agent_kind: "acp",
        llm_model: "openai/gpt-5",
        trigger: "gui",
        execution_status: "paused",
      },
    ],
  },
  {
    id: "5",
    name: "Northwind Labs",
    conversations: 66,
    active: 0,
    spend: 620,
    agents: [{ agent_name: "OpenHands", share: 1 }],
    models: [
      {
        model_name: "openhands/claude-sonnet-4",
        conv: 66,
        tokens: 1_800_000,
        cost: 620,
      },
    ],
    users: [
      {
        user_id: "u-6",
        user_name: "Casey Nguyen",
        user_email: "it@northwind.example",
        conversation_count: 16,
        first_conversation_at: "2026-04-10T08:12:00Z",
        last_conversation_at: "2026-08-22T19:05:00Z",
        first_login_at: "2026-04-08T11:00:00Z",
        last_login_at: "2026-08-22T18:50:00Z",
        spend_mtd: 0,
        spend_ytd: 620,
        spend_lifetime: 620,
        budget_is_disabled: true,
        prs_merged: 0,
      },
    ],
    conversationRows: [
      {
        id: "c-nw-1",
        user_email: "it@northwind.example",
        title: "Archive stale evaluation runs",
        total_tokens: 12800,
        accumulated_cost: 2.4,
        created_at: "2026-08-22T16:10:00Z",
        updated_at: "2026-08-22T19:05:00Z",
        agent_kind: "openhands",
        llm_model: "openhands/claude-sonnet-4",
        trigger: "gui",
        execution_status: "finished",
      },
    ],
  },
];

function scale(value: number, factor: number) {
  return Math.max(0, Math.round(value * factor));
}

function isoDate(offsetDays: number) {
  const date = new Date(`${ANCHOR_DATE}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - offsetDays);
  return date.toISOString().slice(0, 10);
}

function dailyUsage(orgIndex: number, baseConversations: number, days: number) {
  const average = baseConversations / days;
  return Array.from({ length: days }, (_, index) => {
    const age = days - 1 - index;
    const wave = 0.72 + ((Math.sin(orgIndex + age / 2.4) + 1) / 2) * 0.56;
    return {
      date: isoDate(age),
      conversations: Math.max(0, Math.round(average * wave)),
    };
  });
}

function snapshotForSeed(
  seed: OrgUsageSeed,
  orgIndex: number,
  window: SuperAdminUsageWindow,
): SuperAdminOrgUsageSnapshot {
  const factor = WINDOW_SCALE[window];
  const days = WINDOW_DAYS[window];
  const conversations = scale(seed.conversations, factor);
  const spend = Number((seed.spend * factor).toFixed(2));

  return {
    orgId: seed.id,
    orgName: seed.name,
    conversations,
    activeConversations: seed.active,
    spend,
    dailyUsage: dailyUsage(orgIndex, conversations, days),
    agentUsage: seed.agents.map((agent) => ({
      agent_name: agent.agent_name,
      total_cost: Number((spend * agent.share).toFixed(2)),
    })),
    modelUsage: seed.models.map((model) => ({
      model_name: model.model_name,
      conversation_count: scale(model.conv, factor),
      total_tokens: scale(model.tokens, factor),
      total_cost: Number((model.cost * factor).toFixed(2)),
    })),
    users: seed.users.map((user) => ({
      ...user,
      org_name: seed.name,
      conversation_count: scale(user.conversation_count, factor),
      spend_mtd: Number((user.spend_mtd * Math.min(factor, 1)).toFixed(2)),
      spend_ytd: Number(
        (user.spend_ytd * Math.min(factor / 4, 1.2)).toFixed(2),
      ),
    })),
    conversationRows: seed.conversationRows.map((row) => ({
      ...row,
      org_name: seed.name,
    })),
  };
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

export function getSuperAdminUsageView(
  selectedOrgIds: string[],
  timeWindow: string,
): SuperAdminUsageView {
  const window =
    timeWindow === "7d" ||
    timeWindow === "30d" ||
    timeWindow === "90d" ||
    timeWindow === "ytd"
      ? timeWindow
      : "30d";
  const allSnapshots = ORG_USAGE_SEEDS.map((seed, index) =>
    snapshotForSeed(seed, index, window),
  );
  const snapshots =
    selectedOrgIds.length === 0
      ? allSnapshots
      : allSnapshots.filter((snapshot) =>
          selectedOrgIds.includes(snapshot.orgId),
        );

  return mergeSuperAdminSnapshots(selectedOrgIds, snapshots);
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
