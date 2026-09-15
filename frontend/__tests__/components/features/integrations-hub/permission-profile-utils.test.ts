import { describe, expect, it } from "vitest";
import {
  buildPermissionProfileEditorRows,
  editorRowsToSnapshot,
  emptyPermissionProfileSnapshot,
  formatPermissionProfileSummary,
  permissionProfileSnapshotsEqual,
} from "#/components/features/integrations-hub/permission-profile-utils";
import type { HubIntegration } from "#/types/integrations-hub";

const slack: HubIntegration = {
  slug: "slack",
  name: "Slack",
  description: "Chat",
  connected: true,
  enabled: true,
  authStrategy: "oauth2",
  toolCount: 1,
  provider: "Slack",
  kind: "Messaging",
  tools: [
    {
      name: "list_channels",
      description: "List channels",
      accessMode: "enabled",
      defaultScopes: ["channels:read"],
    },
  ],
};

describe("permission-profile-utils", () => {
  it("starts scratch rows disabled and converts them back to a snapshot", () => {
    const rows = buildPermissionProfileEditorRows(
      emptyPermissionProfileSnapshot(),
      [slack],
    );

    expect(rows).toEqual([
      {
        key: "slack",
        name: "Slack",
        enabled: false,
        installed: true,
        tools: [
          {
            name: "list_channels",
            description: "List channels",
            accessMode: "disabled",
            missing: false,
          },
        ],
      },
    ]);
    expect(editorRowsToSnapshot(rows)).toEqual({
      integrations: {
        slack: {
          enabled: false,
          tools: { list_channels: "disabled" },
        },
      },
    });
  });

  it("summarizes enabled integrations and tools", () => {
    expect(
      formatPermissionProfileSummary(
        {
          integrations: {
            slack: {
              enabled: true,
              tools: { list_channels: "enabled", post_message: "disabled" },
            },
          },
        },
        [slack],
      ),
    ).toBe("Slack · 1 tools enabled");
  });

  it("compares snapshots by stable payload", () => {
    expect(
      permissionProfileSnapshotsEqual(
        { integrations: { slack: { enabled: true, tools: { a: "enabled" } } } },
        { integrations: { slack: { enabled: true, tools: { a: "enabled" } } } },
      ),
    ).toBe(true);
    expect(
      permissionProfileSnapshotsEqual(
        { integrations: { slack: { enabled: true, tools: { a: "enabled" } } } },
        {
          integrations: { slack: { enabled: false, tools: { a: "enabled" } } },
        },
      ),
    ).toBe(false);
  });
});
