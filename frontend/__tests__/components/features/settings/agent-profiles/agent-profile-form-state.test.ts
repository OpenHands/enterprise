import { describe, expect, it } from "vitest";
import {
  AgentProfileFormDeps,
  buildAgentProfileSaveInput,
  newAgentProfileFormState,
} from "#/components/features/settings/agent-profiles/agent-profile-form-state";

const deps: AgentProfileFormDeps = {
  acpProviders: [],
  defaultLlmProfileRef: "Default",
  schema: null,
};

describe("buildAgentProfileSaveInput MCP server references", () => {
  it("sends no MCP servers for an org profile while org MCP listings are off", () => {
    const orgDeps = { ...deps, profileScope: "org" as const };
    const state = {
      ...newAgentProfileFormState(orgDeps),
      mcpMode: "custom" as const,
      mcpSelected: ["datadog-shared"],
    };

    const input = buildAgentProfileSaveInput(state, orgDeps);

    expect(input.mcp_server_refs).toEqual([]);
  });

  it("sends the chosen organization-managed servers for an org profile when on", () => {
    const orgDeps = {
      ...deps,
      profileScope: "org" as const,
      orgMcpServersEnabled: true,
    };
    const state = {
      ...newAgentProfileFormState(orgDeps),
      mcpMode: "custom" as const,
      mcpSelected: ["datadog-shared"],
    };

    const input = buildAgentProfileSaveInput(state, orgDeps);

    expect(input.mcp_server_refs).toEqual(["datadog-shared"]);
    // Nothing chosen stays an explicit "none", never "all".
    expect(
      buildAgentProfileSaveInput({ ...state, mcpSelected: [] }, orgDeps)
        .mcp_server_refs,
    ).toEqual([]);
  });

  it("keeps the personal semantics: null for all servers, the list when chosen", () => {
    const personalDeps = { ...deps, orgMcpServersEnabled: true };
    const standard = newAgentProfileFormState(personalDeps);

    expect(
      buildAgentProfileSaveInput(standard, personalDeps).mcp_server_refs,
    ).toBeNull();
    expect(
      buildAgentProfileSaveInput(
        { ...standard, mcpMode: "custom", mcpSelected: ["a", "bifrost"] },
        personalDeps,
      ).mcp_server_refs,
    ).toEqual(["a", "bifrost"]);
  });
});
