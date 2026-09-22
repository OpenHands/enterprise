import { describe, expect, it } from "vitest";
import {
  accessRequestToHubApproval,
  hubAccessModeToApi,
  integrationSpecToHubIntegration,
  managedConnectorToHubIntegration,
  overviewToHub,
  permissionProfileToHub,
} from "#/api/integrations-hub/integrations-hub-adapters";

describe("integrations hub adapters", () => {
  it("maps IntegrationSpec tools dict onto HubIntegration", () => {
    const mapped = integrationSpecToHubIntegration({
      key: "slack",
      name: "Slack",
      provider: "MCP",
      kind: "Messaging",
      authStrategy: "oauth2",
      enabled: true,
      tools: {
        post_message: {
          name: "post_message",
          description: "Post a message",
          accessMode: "approval_required",
          defaultScopes: ["chat:write"],
          lastInvokedAt: "2026-01-01T00:00:00Z",
        },
      },
    });

    expect(mapped.slug).toBe("slack");
    expect(mapped.connected).toBe(true);
    expect(mapped.tools).toHaveLength(1);
    expect(mapped.tools[0]?.accessMode).toBe("approval");
    expect(mapped.tools[0]?.lastUsedAt).toBe("2026-01-01T00:00:00Z");
  });

  it("maps managed connectors and installed overlays", () => {
    const mapped = managedConnectorToHubIntegration(
      {
        slug: "notion",
        name: "Notion",
        description: "Docs",
        authStrategy: "oauth2",
        provider: "mcp",
        tools: [{ name: "search", accessMode: "enabled" }],
      },
      {
        key: "notion",
        name: "Notion",
        enabled: false,
        tools: {
          search: { name: "search", accessMode: "disabled" },
        },
      },
    );

    expect(mapped.connected).toBe(true);
    expect(mapped.enabled).toBe(false);
    expect(mapped.tools[0]?.accessMode).toBe("disabled");
  });

  it("maps approvals, profiles, and overview payloads", () => {
    const approval = accessRequestToHubApproval({
      id: "req-1",
      integrationKey: "slack",
      toolName: "post_message",
      createdAt: "2026-01-01T00:00:00Z",
      status: "pending",
      requestedMinutes: 60,
    });
    expect(approval.status).toBe("pending");
    expect(approval.integrationName).toBe("slack");

    const profile = permissionProfileToHub({
      id: "p1",
      name: "Default",
      agentApiKey: "cla_test",
      snapshot: {
        integrations: {
          slack: { enabled: true, tools: { post_message: "enabled" } },
        },
      },
    });
    expect(profile.agentApiKey).toBe("cla_test");
    expect(profile.snapshot?.integrations.slack?.tools.post_message).toBe(
      "enabled",
    );

    const overview = overviewToHub({
      users: [
        {
          ownerId: "u1",
          totalConnections: 1,
          totalAccessRequests: 0,
          pendingAccessRequests: 0,
          notifications: 0,
          integrations: ["slack"],
          providers: ["mcp"],
          connections: [],
        },
      ],
      duplicateGroups: [
        {
          provider: "slack",
          externalAccountId: "T1",
          ownerIds: ["u1", "u2"],
        },
      ],
    });
    expect(overview.overviewUsers).toHaveLength(1);
    expect(overview.duplicateGroups[0]?.ownerIds).toEqual(["u1", "u2"]);
  });

  it("round-trips tool access modes for Hub writes", () => {
    expect(hubAccessModeToApi("approval")).toBe("approval_required");
    expect(hubAccessModeToApi("disabled")).toBe("disabled");
    expect(hubAccessModeToApi("enabled")).toBe("enabled");
  });
});
