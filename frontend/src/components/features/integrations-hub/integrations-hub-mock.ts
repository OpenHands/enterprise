import { OFFICIAL_HUB_CATALOG } from "#/components/features/integrations-hub/hub-official-catalog";
import type {
  HubApiKey,
  HubApproval,
  HubDuplicateGroup,
  HubIntegration,
  HubOverviewUser,
  HubPermissionProfile,
  HubTool,
  HubUserRequest,
} from "#/types/integrations-hub";

function daysAgo(days: number): string {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
}

function tools(
  entries: Array<
    [
      string,
      string,
      HubTool["accessMode"],
      string[],
      (number | null)?,
      HubTool["accessMode"]?,
    ]
  >,
): HubTool[] {
  return entries.map(
    ([
      name,
      description,
      accessMode,
      defaultScopes,
      unusedDays,
      maxAccessMode,
    ]) => {
      let lastUsedAt: string | undefined;
      if (unusedDays !== null) {
        lastUsedAt = daysAgo(unusedDays ?? 2);
      }
      return {
        name,
        description,
        accessMode,
        defaultScopes,
        lastUsedAt,
        maxAccessMode,
      };
    },
  );
}

const ALL_INTEGRATIONS: Omit<HubIntegration, "connected" | "enabled">[] = [
  {
    slug: "slack",
    name: "Slack",
    description: "Post messages and read channel context on the user's behalf.",
    authStrategy: "oauth2",
    toolCount: 3,
    provider: "Slack",
    kind: "Messaging",
    tools: tools([
      [
        "post_message",
        "Post a message to a Slack channel.",
        "approval",
        ["chat:write"],
        90,
      ],
      [
        "list_channels",
        "List public channels the user can see.",
        "enabled",
        ["channels:read"],
      ],
      [
        "read_thread",
        "Read messages in a Slack thread.",
        "enabled",
        ["channels:history"],
        null,
      ],
    ]),
  },
  {
    slug: "github",
    name: "GitHub",
    description: "Open issues, review pull requests, and search repositories.",
    authStrategy: "oauth2",
    toolCount: 3,
    provider: "GitHub",
    kind: "Source control",
    tools: tools([
      [
        "create_issue",
        "Open a GitHub issue in the selected repository.",
        "approval",
        ["issues:write"],
        80,
      ],
      [
        "request_review",
        "Request review on a pull request.",
        "approval",
        ["pull_requests:write"],
      ],
      [
        "search_repos",
        "Search repositories the user can access.",
        "enabled",
        ["repo:read"],
      ],
    ]),
  },
  {
    slug: "linear",
    name: "Linear",
    description: "Create and update issues in Linear workspaces.",
    authStrategy: "oauth2",
    toolCount: 22,
    provider: "Linear",
    kind: "Issue tracking",
    tools: tools([
      [
        "create_issue",
        "Create a Linear issue.",
        "approval",
        ["issues:write"],
        80,
        "approval",
      ],
      ["list_issues", "List Linear issues.", "enabled", ["issues:read"]],
      ["get_issue", "Get a Linear issue by ID.", "enabled", ["issues:read"]],
      [
        "update_issue",
        "Update title, description, or status on an issue.",
        "approval",
        ["issues:write"],
      ],
      [
        "search_issues",
        "Search issues across teams and projects.",
        "enabled",
        ["issues:read"],
      ],
      [
        "assign_issue",
        "Assign an issue to a workspace member.",
        "approval",
        ["issues:write"],
      ],
      [
        "add_comment",
        "Comment on a Linear issue.",
        "approval",
        ["comments:write"],
      ],
      [
        "list_comments",
        "List comments on an issue.",
        "enabled",
        ["comments:read"],
      ],
      [
        "archive_issue",
        "Archive a Linear issue.",
        "disabled",
        ["issues:write"],
        90,
        "approval",
      ],
      ["list_projects", "List Linear projects.", "enabled", ["projects:read"]],
      [
        "create_project",
        "Create a Linear project.",
        "approval",
        ["projects:write"],
      ],
      [
        "update_project",
        "Update a Linear project.",
        "approval",
        ["projects:write"],
      ],
      ["list_teams", "List teams in the workspace.", "enabled", ["teams:read"]],
      ["get_team", "Get a Linear team by ID.", "enabled", ["teams:read"]],
      ["list_cycles", "List cycles for a team.", "enabled", ["cycles:read"]],
      ["create_cycle", "Create a team cycle.", "approval", ["cycles:write"]],
      ["list_labels", "List issue labels.", "enabled", ["labels:read"]],
      ["create_label", "Create an issue label.", "approval", ["labels:write"]],
      ["list_users", "List workspace members.", "enabled", ["users:read"]],
      [
        "list_workflow_states",
        "List workflow states for a team.",
        "enabled",
        ["issues:read"],
      ],
      [
        "create_document",
        "Create a Linear document.",
        "approval",
        ["documents:write"],
      ],
      [
        "list_documents",
        "List Linear documents.",
        "enabled",
        ["documents:read"],
        null,
      ],
    ]),
  },
  {
    slug: "jira",
    name: "Jira",
    description: "Search and transition Jira issues with the user's account.",
    authStrategy: "oauth2",
    toolCount: 2,
    provider: "Atlassian",
    kind: "Issue tracking",
    tools: tools([
      ["search_issues", "Search Jira issues.", "enabled", ["issues:read"]],
      [
        "transition_issue",
        "Transition a Jira issue.",
        "approval",
        ["issues:write"],
      ],
    ]),
  },
  {
    slug: "notion",
    name: "Notion",
    description: "Read and update pages, databases, and comments in Notion.",
    authStrategy: "oauth2",
    toolCount: 2,
    provider: "Notion",
    kind: "Docs",
    tools: tools([
      ["read_page", "Read a Notion page.", "enabled", ["pages:read"]],
      ["delete_page", "Delete a Notion page.", "disabled", ["pages:write"]],
    ]),
  },
  {
    slug: "figma",
    name: "Figma",
    description: "Inspect files, comments, and design tokens from Figma.",
    authStrategy: "oauth2",
    toolCount: 2,
    provider: "Figma",
    kind: "Design",
    tools: tools([
      ["list_files", "List Figma files.", "enabled", ["files:read"]],
      [
        "read_comments",
        "Read comments on a Figma file.",
        "enabled",
        ["comments:read"],
      ],
    ]),
  },
];

const ORG_APPROVED_SLUGS = new Set(["slack", "github", "linear"]);
const INITIAL_CONNECTED_SLUGS = new Set(["slack"]);

function withConnectionState(
  item: Omit<HubIntegration, "connected" | "enabled">,
): HubIntegration {
  const connected = INITIAL_CONNECTED_SLUGS.has(item.slug);
  return { ...item, connected, enabled: connected };
}

export function mockRequestableCatalog(): HubIntegration[] {
  return ALL_INTEGRATIONS.filter(
    (item) => !ORG_APPROVED_SLUGS.has(item.slug),
  ).map(withConnectionState);
}

export function mockIntegrationsForWorkspace(
  isPersonalWorkspace: boolean,
): HubIntegration[] {
  const source = isPersonalWorkspace
    ? ALL_INTEGRATIONS
    : ALL_INTEGRATIONS.filter((item) => ORG_APPROVED_SLUGS.has(item.slug));
  return source.map(withConnectionState);
}

export function mockCatalogIntegrations(): HubIntegration[] {
  const detailed = new Map(
    ALL_INTEGRATIONS.map((item) => [item.slug, withConnectionState(item)]),
  );
  return OFFICIAL_HUB_CATALOG.map((item) => {
    const local = detailed.get(item.slug);
    return local
      ? {
          ...item,
          connected: local.connected,
          enabled: local.enabled,
          tools: local.tools.length > 0 ? local.tools : item.tools,
          toolCount: local.tools.length || item.toolCount,
          logoUrl: item.logoUrl ?? local.logoUrl,
        }
      : withConnectionState(item);
  });
}

export function mockApprovals(): HubApproval[] {
  return [
    {
      id: "apr-1",
      toolName: "post_message",
      integrationKey: "slack",
      integrationName: "Slack",
      status: "pending",
      agentId: "agent-local",
      agentClass: "OpenHands",
      justification: "Post the standup recap to #eng.",
      scopes: ["chat:write"],
      requestedMinutes: 120,
      createdAt: "2026-09-09T15:04:00Z",
      conversationId: "1",
    },
    {
      id: "apr-2",
      toolName: "create_issue",
      integrationKey: "github",
      integrationName: "GitHub",
      status: "pending",
      agentId: "agent-ci",
      agentClass: "Automation",
      justification: "Open a follow-up issue after the failing test.",
      scopes: ["issues:write"],
      requestedMinutes: 60,
      createdAt: "2026-09-09T16:22:00Z",
      conversationId: "2",
    },
    {
      id: "apr-3",
      toolName: "create_issue",
      integrationKey: "linear",
      integrationName: "Linear",
      status: "pending",
      agentId: "agent-local",
      agentClass: "OpenHands",
      scopes: ["issues:write"],
      requestedMinutes: 180,
      createdAt: "2026-09-10T09:11:00Z",
      conversationId: "1",
    },
    {
      id: "apr-4",
      toolName: "request_review",
      integrationKey: "github",
      integrationName: "GitHub",
      status: "pending",
      agentId: "agent-local",
      agentClass: "OpenHands",
      justification: "Request review on the Hub nav PR.",
      scopes: ["pull_requests:write"],
      requestedMinutes: 90,
      createdAt: "2026-09-10T11:40:00Z",
      conversationId: "3",
    },
    {
      id: "apr-5",
      toolName: "list_channels",
      integrationKey: "slack",
      integrationName: "Slack",
      status: "approved",
      agentId: "agent-local",
      agentClass: "OpenHands",
      scopes: ["channels:read"],
      requestedMinutes: 60,
      createdAt: "2026-09-08T13:00:00Z",
      decidedBy: "you",
      decidedAt: "2026-09-08T13:05:00Z",
      conversationId: "1",
    },
    {
      id: "apr-6",
      toolName: "delete_page",
      integrationKey: "notion",
      integrationName: "Notion",
      status: "denied",
      agentId: "agent-ci",
      agentClass: "Automation",
      scopes: ["pages:write"],
      requestedMinutes: 30,
      createdAt: "2026-09-07T18:20:00Z",
      decidedBy: "you",
      decidedAt: "2026-09-07T18:22:00Z",
      conversationId: "2",
    },
  ];
}

export function mockUserRequests(): HubUserRequest[] {
  return [
    {
      id: "req-1",
      name: "Notion",
      slug: "notion",
      requestedBy: "Alex Chen",
      notes: "Need page comments in standup recaps.",
      source: "catalog",
      createdAt: "2026-09-08T10:12:00Z",
    },
    {
      id: "req-2",
      name: "Figma",
      slug: "figma",
      requestedBy: "Jordan Blake",
      notes: "Inspect design tokens from product files.",
      source: "catalog",
      createdAt: "2026-09-09T08:44:00Z",
    },
    {
      id: "req-3",
      name: "Acme CRM",
      slug: "acme-crm",
      requestedBy: "Sam Ortiz",
      description:
        "Accounts, opportunities, and follow-up tasks for the sales team.",
      docsUrl: "https://example.com/docs",
      notes:
        "Need read access to open deals so standup recaps can mention blocked opportunities. We have a read-only API key in 1Password.",
      source: "custom",
      createdAt: "2026-09-10T07:18:00Z",
    },
  ];
}

export function mockApiKeys(): HubApiKey[] {
  return [
    {
      id: "key-1",
      name: "User automation key",
      value: "ohk_live_4f2a9c81d0be",
    },
    {
      id: "key-2",
      name: "Admin automation key",
      value: "ohk_admin_91c8e3ab77f2",
    },
  ];
}

export function mockPermissionProfiles(): HubPermissionProfile[] {
  return [
    {
      id: "profile-1",
      name: "Read-only tools",
      summary: "Slack, GitHub · 8 tools enabled",
      updatedAt: "2026-09-06T12:00:00Z",
      isDefault: true,
      agentApiKey: "ohk_prof_readonly_aa11",
    },
    {
      id: "profile-2",
      name: "Write to approved integrations",
      summary: "Slack, GitHub, Linear · 21 tools enabled",
      updatedAt: "2026-09-09T17:30:00Z",
      agentApiKey: "ohk_prof_write_bb22",
    },
  ];
}

const GITHUB_DUPLICATE: HubDuplicateGroup = {
  provider: "GitHub",
  externalAccountId: "octocat",
  ownerIds: ["alex.chen@acme.org", "jordan.blake@acme.org"],
  displayNames: ["Alex Chen", "Jordan Blake"],
};

export function mockOverviewUsers(): HubOverviewUser[] {
  return [
    {
      ownerId: "alex.chen@acme.org",
      totalConnections: 3,
      totalAccessRequests: 7,
      pendingAccessRequests: 2,
      notifications: 5,
      integrations: ["Slack", "GitHub", "Linear"],
      providers: ["Slack", "GitHub", "Linear"],
      connections: [
        {
          integrationKey: "slack",
          provider: "Slack",
          displayName: "Alex Chen",
          externalAccountId: "U0ALEX",
          authStrategy: "oauth2",
          updatedAt: "2026-09-08T12:00:00Z",
        },
        {
          integrationKey: "github",
          provider: "GitHub",
          displayName: "octocat",
          externalAccountId: "octocat",
          authStrategy: "oauth2",
          updatedAt: "2026-09-09T09:00:00Z",
        },
        {
          integrationKey: "linear",
          provider: "Linear",
          displayName: "Alex Chen",
          externalAccountId: "alex-chen",
          authStrategy: "oauth2",
          updatedAt: "2026-09-07T16:00:00Z",
        },
      ],
      duplicateExternalAccounts: [GITHUB_DUPLICATE],
    },
    {
      ownerId: "jordan.blake@acme.org",
      totalConnections: 1,
      totalAccessRequests: 1,
      pendingAccessRequests: 0,
      notifications: 1,
      integrations: ["GitHub"],
      providers: ["GitHub"],
      connections: [
        {
          integrationKey: "github",
          provider: "GitHub",
          displayName: "octocat",
          externalAccountId: "octocat",
          authStrategy: "oauth2",
          updatedAt: "2026-09-09T10:00:00Z",
        },
      ],
      duplicateExternalAccounts: [GITHUB_DUPLICATE],
    },
    {
      ownerId: "sam.ortiz@acme.org",
      totalConnections: 2,
      totalAccessRequests: 4,
      pendingAccessRequests: 1,
      notifications: 3,
      integrations: ["Slack", "Linear"],
      providers: ["Slack", "Linear"],
      connections: [
        {
          integrationKey: "slack",
          provider: "Slack",
          displayName: "Sam Ortiz",
          externalAccountId: "U0SAM",
          authStrategy: "oauth2",
          updatedAt: "2026-09-06T11:00:00Z",
        },
        {
          integrationKey: "linear",
          provider: "Linear",
          displayName: "Sam Ortiz",
          externalAccountId: "sam-ortiz",
          authStrategy: "oauth2",
          updatedAt: "2026-09-08T14:00:00Z",
        },
      ],
      duplicateExternalAccounts: [],
    },
  ];
}

export function mockDuplicateGroups(): HubDuplicateGroup[] {
  return [GITHUB_DUPLICATE];
}
