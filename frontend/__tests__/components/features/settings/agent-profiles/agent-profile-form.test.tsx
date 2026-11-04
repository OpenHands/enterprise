import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentProfileForm } from "#/components/features/settings/agent-profiles/agent-profile-form";
import {
  AgentProfileFormDeps,
  AgentProfileFormState,
  newAgentProfileFormState,
} from "#/components/features/settings/agent-profiles/agent-profile-form-state";
import type { ProfileScopeItem } from "#/components/features/settings/agent-profiles/profile-scope-list";
import type { AcpCredentialForm } from "#/hooks/use-acp-credential-form";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options?.names ? `${key}:${options.names}` : key,
  }),
}));

const deps: AgentProfileFormDeps = {
  acpProviders: [],
  defaultLlmProfileRef: "Default",
  schema: null,
};

const credentialForm: AcpCredentialForm = {
  fields: [],
  values: {},
  setValue: () => {},
  secretExists: () => false,
  hasValueFor: () => false,
  conflicts: [],
  isDirty: false,
  save: async () => true,
  reset: () => {},
  isSaving: false,
};

const ORG_MCP_ITEMS: ProfileScopeItem[] = [
  {
    name: "datadog-shared",
    description: "https://dd.example/mcp",
    scope: "organization",
  },
];

function renderForm({
  state,
  scope,
  mcpServers,
  orgMcpServersEnabled,
  onChange = vi.fn(),
}: {
  state: AgentProfileFormState;
  scope: "personal" | "org";
  mcpServers: ProfileScopeItem[];
  orgMcpServersEnabled?: boolean;
  onChange?: (patch: Partial<AgentProfileFormState>) => void;
}) {
  render(
    <AgentProfileForm
      state={state}
      onChange={onChange}
      isDisabled={false}
      isAcpEnabled={false}
      acpProviders={[]}
      llmProfiles={[]}
      schema={null}
      tools={[]}
      mcpServers={mcpServers}
      secrets={[]}
      skills={[]}
      credentialForm={credentialForm}
      profileScope={scope}
      orgMcpServersEnabled={orgMcpServersEnabled}
    />,
  );
  return onChange;
}

describe("AgentProfileForm MCP servers", () => {
  it("still leaves the MCP section out of an org profile while the flag is off", () => {
    renderForm({
      state: newAgentProfileFormState({ ...deps, profileScope: "org" }),
      scope: "org",
      mcpServers: ORG_MCP_ITEMS,
    });

    expect(
      screen.queryByTestId("agent-profile-mcp-list"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("agent-profile-mcp-mode"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("agent-profile-mcp-hint"),
    ).not.toBeInTheDocument();
  });

  it("lets an org profile toggle the organization-managed servers, with no 'all' mode", () => {
    const onChange = renderForm({
      state: {
        ...newAgentProfileFormState({ ...deps, profileScope: "org" }),
        mcpMode: "custom",
        mcpSelected: [],
      },
      scope: "org",
      mcpServers: ORG_MCP_ITEMS,
      orgMcpServersEnabled: true,
    });

    expect(
      screen.queryByTestId("agent-profile-mcp-mode"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("agent-profile-mcp-hint")).toHaveTextContent(
      "SETTINGS$AGENT_PROFILE_ORG_MCP_HINT",
    );
    const toggle = screen.getByTestId("agent-profile-mcp-datadog-shared");
    expect(toggle).not.toBeChecked();
    expect(toggle).not.toBeDisabled();

    fireEvent.click(toggle);

    expect(onChange).toHaveBeenCalledWith({ mcpSelected: ["datadog-shared"] });
  });

  it("tells an org admin when there is nothing to pick yet", () => {
    renderForm({
      state: {
        ...newAgentProfileFormState({ ...deps, profileScope: "org" }),
        mcpMode: "custom",
      },
      scope: "org",
      mcpServers: [],
      orgMcpServersEnabled: true,
    });

    expect(screen.getByTestId("agent-profile-mcp-none")).toHaveTextContent(
      "SETTINGS$AGENT_PROFILE_ORG_MCP_NONE",
    );
  });

  it("flags an org profile's reference to a listing that no longer exists", () => {
    renderForm({
      state: {
        ...newAgentProfileFormState({ ...deps, profileScope: "org" }),
        mcpMode: "custom",
        mcpSelected: ["gone"],
      },
      scope: "org",
      mcpServers: ORG_MCP_ITEMS,
      orgMcpServersEnabled: true,
    });

    expect(screen.getByTestId("agent-profile-mcp-dangling")).toHaveTextContent(
      "SETTINGS$AGENT_PROFILE_MCP_DANGLING:gone",
    );
  });

  it("badges the organization's listings in a personal profile's picker", () => {
    renderForm({
      state: {
        ...newAgentProfileFormState(deps),
        mcpMode: "custom",
        mcpSelected: ["mine"],
      },
      scope: "personal",
      mcpServers: [{ name: "mine", description: null }, ...ORG_MCP_ITEMS],
      orgMcpServersEnabled: true,
    });

    expect(screen.getByTestId("agent-profile-mcp-mode")).toBeInTheDocument();
    expect(
      screen.getByTestId("agent-profile-mcp-datadog-shared-org-badge"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("agent-profile-mcp-mine-org-badge"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("agent-profile-mcp-hint")).toHaveTextContent(
      "SETTINGS$AGENT_PROFILE_MCP_CHOOSE_HINT",
    );
  });
});
