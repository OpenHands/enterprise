import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import userEvent from "@testing-library/user-event";
import AppSettingsScreen from "#/routes/app-settings";
import AgentProfilesService from "#/api/agent-profiles-service/agent-profiles-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { SandboxService } from "#/api/sandbox-service/sandbox-service.api";
import SettingsService from "#/api/settings-service/settings-service.api";
import { MOCK_DEFAULT_USER_SETTINGS } from "#/mocks/handlers";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";

const DEFAULT_IMAGE = "registry.example/agent-server:default";
const PROFILE_IMAGE = "registry.example/agent-server:data-science";
const PERSONAL_IMAGE = "registry.example/agent-server:personal";

function arrange({
  personalImage = null,
  profileImage = PROFILE_IMAGE,
  offered = [DEFAULT_IMAGE, PROFILE_IMAGE, PERSONAL_IMAGE],
  deploymentMode = "cloud",
  agentProfilesPage = false,
}: {
  personalImage?: string | null;
  profileImage?: string | null;
  offered?: string[];
  deploymentMode?: "cloud" | "self_hosted";
  agentProfilesPage?: boolean;
} = {}) {
  const base = createMockWebClientConfig();
  vi.spyOn(OptionService, "getConfig").mockResolvedValue(
    createMockWebClientConfig({
      app_mode: "saas",
      feature_flags: {
        ...base.feature_flags,
        deployment_mode: deploymentMode,
        enable_agent_profiles_page: agentProfilesPage,
      },
    }),
  );
  vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
    ...MOCK_DEFAULT_USER_SETTINGS,
    default_sandbox_spec_id: personalImage,
  });
  const save = vi
    .spyOn(SettingsService, "saveSettings")
    .mockResolvedValue(true);
  vi.spyOn(SandboxService, "searchSandboxSpecs").mockResolvedValue({
    items: offered.map((id) => ({
      id,
      command: null,
      created_at: "2026-01-01T00:00:00Z",
      initial_env: {},
      working_dir: "/workspace",
    })),
    next_page_id: null,
  });
  const list = vi
    .spyOn(AgentProfilesService, "listProfiles")
    .mockResolvedValue({
      profiles: [
        {
          id: "org-default",
          name: "team",
          agent_kind: "openhands",
          revision: 1,
          llm_profile_ref: "Default",
          mcp_server_refs: [],
          scope: "org",
        },
      ],
      active_agent_profile_id: "org-default",
      org_default_agent_profile_id: "org-default",
    });
  vi.spyOn(AgentProfilesService, "getProfile").mockResolvedValue({
    name: "team",
    scope: "org",
    profile: {
      agent_kind: "openhands",
      id: "org-default",
      name: "team",
      revision: 1,
      llm_profile_ref: "Default",
      sandbox: profileImage ? { spec_id: profileImage } : null,
    },
  });
  render(
    <MemoryRouter>
      <AppSettingsScreen />
    </MemoryRouter>,
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={new QueryClient()}>
          {children}
        </QueryClientProvider>
      ),
    },
  );
  return { save, list };
}

const input = () => screen.findByTestId("default-sandbox-spec-input");

beforeEach(() => {
  useSelectedOrganizationStore.setState({ organizationId: "test-org-id" });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Default Sandbox inherited from the agent profile", () => {
  it("shows the effective profile's image as inherited when none is saved", async () => {
    arrange();

    await waitFor(async () =>
      expect(await input()).toHaveAttribute(
        "placeholder",
        "SETTINGS$DEFAULT_SANDBOX_SPEC_INHERITED",
      ),
    );
    expect(
      screen.getByTestId("default-sandbox-spec-inherited-hint"),
    ).toBeInTheDocument();
    expect(await input()).toHaveValue("");
  });

  it.each([
    ["the profile has no image", { profileImage: null }],
    ["the profile's image is retired", { offered: [DEFAULT_IMAGE] }],
    [
      "agent profiles are off on a self-hosted instance",
      { deploymentMode: "self_hosted" as const },
    ],
  ])("falls back to the system default when %s", async (_label, options) => {
    const { list } = arrange(options);

    await waitFor(() =>
      expect(SandboxService.searchSandboxSpecs).toHaveBeenCalled(),
    );
    expect(await input()).toHaveAttribute(
      "placeholder",
      "SETTINGS$DEFAULT_SANDBOX_SPEC_PLACEHOLDER",
    );
    expect(
      screen.queryByTestId("default-sandbox-spec-inherited-hint"),
    ).not.toBeInTheDocument();
    if (options.deploymentMode === "self_hosted") {
      expect(list).not.toHaveBeenCalled();
    }
  });

  it("clears a saved personal image back to inheritance without saving the inherited one", async () => {
    const { save } = arrange({ personalImage: PERSONAL_IMAGE });

    await waitFor(async () =>
      expect(await input()).toHaveValue(PERSONAL_IMAGE),
    );
    expect(
      screen.queryByTestId("default-sandbox-spec-inherited-hint"),
    ).not.toBeInTheDocument();

    // HeroUI's clear button: the one that is not the suggestions toggle.
    const clear = (await input())
      .closest("label")!
      .querySelector<HTMLButtonElement>("button:not([aria-haspopup])")!;
    await userEvent.click(clear);
    expect(
      await screen.findByTestId("default-sandbox-spec-inherited-hint"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("submit-button"));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        expect.objectContaining({ default_sandbox_spec_id: null }),
      ),
    );
  });
});
