import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AgentProfileSummary } from "#/api/agent-profiles-service/agent-profiles-service.api";
import { AgentProfileForm } from "#/components/features/settings/agent-profiles/agent-profile-form";
import { AgentProfileRow } from "#/components/features/settings/agent-profiles/agent-profile-row";
import {
  AgentProfileFormDeps,
  agentProfileFormStateFromProfile,
  buildAgentProfileSaveInput,
  newAgentProfileFormState,
} from "#/components/features/settings/agent-profiles/agent-profile-form-state";
import type { AcpCredentialForm } from "#/hooks/use-acp-credential-form";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options?.image ? `${key}:${options.image}` : key,
  }),
}));

const IMAGE = "registry.example/agent-server:data-science";

const deps: AgentProfileFormDeps = {
  acpProviders: [],
  defaultLlmProfileRef: "Default",
  schema: null,
};

describe("Agent Profile sandbox image", () => {
  it("round-trips the stored image and clears it to the deployment default", () => {
    const state = agentProfileFormStateFromProfile(
      {
        agent_kind: "openhands",
        id: "p1",
        name: "data-science",
        revision: 1,
        llm_profile_ref: "Default",
        sandbox: { spec_id: IMAGE },
      },
      deps,
    );

    expect(state.sandboxSpecId).toBe(IMAGE);
    expect(buildAgentProfileSaveInput(state, deps).sandbox).toEqual({
      spec_id: IMAGE,
    });
    expect(
      buildAgentProfileSaveInput({ ...state, sandboxSpecId: null }, deps)
        .sandbox,
    ).toBeNull();
  });

  it("starts a new profile on the deployment default", () => {
    const state = newAgentProfileFormState(deps);

    expect(state.sandboxSpecId).toBeNull();
    expect(buildAgentProfileSaveInput(state, deps).sandbox).toBeNull();
  });

  it("explains that an unset image falls back to the deployment default", () => {
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
    render(
      <AgentProfileForm
        state={{ ...newAgentProfileFormState(deps), sandboxSpecId: IMAGE }}
        onChange={vi.fn()}
        isDisabled={false}
        isAcpEnabled={false}
        acpProviders={[]}
        llmProfiles={[]}
        schema={null}
        tools={[]}
        mcpServers={[]}
        secrets={[]}
        skills={[]}
        credentialForm={credentialForm}
        sandboxSpecIds={["registry.example/agent-server:default"]}
      />,
    );

    expect(screen.getByTestId("agent-profile-sandbox-image")).toBeVisible();
    expect(
      screen.getByTestId("agent-profile-sandbox-image-hint"),
    ).toHaveTextContent("SETTINGS$AGENT_PROFILE_SANDBOX_IMAGE_HINT");
  });
});

function orgProfile(
  overrides: Partial<AgentProfileSummary> = {},
): AgentProfileSummary {
  return {
    id: "org-1",
    name: "team",
    agent_kind: "openhands",
    revision: 1,
    llm_profile_ref: "Default",
    mcp_server_refs: [],
    scope: "org",
    ...overrides,
  };
}

function renderRow(
  profile: AgentProfileSummary,
  { canManageOrg = true, isOrgDefault = false } = {},
) {
  const onToggleOrgDefault = vi.fn();
  render(
    <AgentProfileRow
      profile={profile}
      isActive={false}
      isOrgDefault={isOrgDefault}
      onActivate={vi.fn()}
      onDeactivate={vi.fn()}
      onEdit={vi.fn()}
      onDelete={vi.fn()}
      onToggleOrgDefault={onToggleOrgDefault}
      isActivating={false}
      canManageOrg={canManageOrg}
    />,
  );
  fireEvent.click(screen.getByTestId("agent-profile-menu-trigger"));
  return onToggleOrgDefault;
}

describe("Organization default agent profile", () => {
  it("lets an admin set an organization profile as the default", () => {
    const onToggle = renderRow(orgProfile());

    fireEvent.click(screen.getByTestId("profile-set-org-default"));

    expect(onToggle).toHaveBeenCalledWith(orgProfile());
  });

  it("badges the default and offers to clear it", () => {
    renderRow(orgProfile(), { isOrgDefault: true });

    expect(
      screen.getByTestId("agent-profile-org-default-badge"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("profile-clear-org-default")).toBeInTheDocument();
  });

  it.each([
    ["a member", orgProfile(), { canManageOrg: false }],
    ["a personal profile", orgProfile({ scope: "personal" }), {}],
    ["a legacy ACP org profile", orgProfile({ agent_kind: "acp" }), {}],
  ])("offers no default action for %s", (_label, profile, options) => {
    renderRow(profile, options);

    expect(
      screen.queryByTestId("profile-set-org-default"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("profile-set-active")).toBeInTheDocument();
  });
});
