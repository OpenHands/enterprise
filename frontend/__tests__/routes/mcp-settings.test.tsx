import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router";
import McpService from "#/api/mcp-service/mcp-service.api";
import { oauthV2Service } from "#/api/oauth-v2-service/oauth-v2-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { OrgMcpServerItem } from "#/api/org-mcp-servers.types";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { SecretsService } from "#/api/secrets-service";
import SettingsService from "#/api/settings-service/settings-service.api";
import { OrganizationMember } from "#/types/org";
import { REDACTED_MCP_SECRET, secretReference } from "#/utils/org-mcp-servers";
import {
  MOCK_DEFAULT_USER_SETTINGS,
  resetTestHandlersMockSettings,
} from "#/mocks/handlers";
import MCPSettingsScreen, { clientLoader } from "#/routes/mcp-settings";
import { SUPER_ADMIN_SETUP_STEP_EVENT } from "#/components/features/setup/tours/types";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { Settings } from "#/types/settings";
import { MOCK_ROLE_PERMISSIONS } from "#/mocks/org-handlers";

function buildSettings(overrides: Partial<Settings> = {}): Settings {
  return {
    ...MOCK_DEFAULT_USER_SETTINGS,
    ...overrides,
    agent_settings: {
      ...MOCK_DEFAULT_USER_SETTINGS.agent_settings,
      ...overrides.agent_settings,
    },
  };
}

function deepMerge(
  base: Record<string, unknown>,
  patch: Record<string, unknown>,
): Record<string, unknown> {
  const result = { ...base };

  for (const [key, value] of Object.entries(patch)) {
    if (
      value != null &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      result[key] != null &&
      typeof result[key] === "object" &&
      !Array.isArray(result[key])
    ) {
      result[key] = deepMerge(
        result[key] as Record<string, unknown>,
        value as Record<string, unknown>,
      );
    } else {
      result[key] = value;
    }
  }

  return result;
}

/** Shows the router's current path, for tests that navigate away. */
function CurrentLocation() {
  const { pathname } = useLocation();
  return <p data-testid="location">{pathname}</p>;
}

function renderMcpSettingsScreen(organizationId = "1") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });

  useSelectedOrganizationStore.setState({ organizationId });

  return render(<MCPSettingsScreen />, {
    wrapper: ({ children }) => (
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          {children}
          <CurrentLocation />
        </QueryClientProvider>
      </MemoryRouter>
    ),
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  resetTestHandlersMockSettings();
  useSelectedOrganizationStore.setState({ organizationId: "1" });
});

describe("MCPSettingsScreen", () => {
  it("renders and saves the built-in Tavily search settings on the MCP page", async () => {
    let persistedSettings = buildSettings({
      search_api_key: "tavily-key",
      search_api_key_set: true,
    });

    vi.spyOn(SettingsService, "getSettings").mockImplementation(async () =>
      structuredClone(persistedSettings),
    );
    const saveSettingsSpy = vi
      .spyOn(SettingsService, "saveSettings")
      .mockImplementation(async (payload) => {
        persistedSettings = buildSettings(
          deepMerge(
            structuredClone(persistedSettings) as Record<string, unknown>,
            payload as Record<string, unknown>,
          ) as Partial<Settings>,
        );
        return true;
      });

    renderMcpSettingsScreen();

    expect(
      await screen.findByTestId("mcp-search-settings-section"),
    ).toBeInTheDocument();
    const searchApiKeyInput = await screen.findByTestId("search-api-key-input");
    expect(searchApiKeyInput).toHaveValue("tavily-key");

    await userEvent.clear(searchApiKeyInput);
    await userEvent.type(searchApiKeyInput, "updated-tavily-key");
    await userEvent.click(screen.getByTestId("save-search-api-key-button"));

    await waitFor(() => {
      expect(saveSettingsSpy).toHaveBeenCalledWith({
        search_api_key: "updated-tavily-key",
      });
    });

    await waitFor(() => {
      expect(screen.getByTestId("search-api-key-input")).toHaveValue(
        "updated-tavily-key",
      );
    });
  });

  it("removes a newly added MCP server after the delete flow completes", async () => {
    let persistedSettings = buildSettings({
      mcp_config: {
        sse_servers: [],
        stdio_servers: [],
        shttp_servers: [],
      },
      agent_settings: {
        mcp_config: {
          sse_servers: [],
          stdio_servers: [],
          shttp_servers: [],
        },
      },
    });

    vi.spyOn(SettingsService, "getSettings").mockImplementation(async () =>
      structuredClone(persistedSettings),
    );
    vi.spyOn(SettingsService, "saveSettings").mockImplementation(
      async (payload) => {
        const nextSettings = structuredClone(persistedSettings) as Record<
          string,
          unknown
        >;
        const agentSettingsPatch = (payload as Record<string, unknown>)
          .agent_settings_diff as Record<string, unknown> | undefined;

        if (agentSettingsPatch) {
          nextSettings.agent_settings = deepMerge(
            (nextSettings.agent_settings ?? {}) as Record<string, unknown>,
            agentSettingsPatch,
          );
        }

        for (const [key, value] of Object.entries(
          payload as Record<string, unknown>,
        )) {
          if (key === "agent_settings_diff") {
            continue;
          }
          nextSettings[key] = value;
        }

        persistedSettings = buildSettings(nextSettings as Partial<Settings>);
        return true;
      },
    );

    renderMcpSettingsScreen();

    await screen.findByText("SETTINGS$MCP_NO_SERVERS");

    await userEvent.click(
      screen.getByRole("button", { name: "SETTINGS$MCP_ADD_SERVER" }),
    );
    await userEvent.type(
      await screen.findByTestId("url-input"),
      "https://mcp.example.com/sse",
    );
    await userEvent.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getAllByTestId("mcp-server-item")).toHaveLength(1);
    });

    await userEvent.click(screen.getByTestId("delete-mcp-server-button"));
    await userEvent.click(await screen.findByTestId("confirm-button"));

    await waitFor(() => {
      expect(screen.queryAllByTestId("mcp-server-item")).toHaveLength(0);
    });
  });

  it("tells the setup guide when an MCP server is added", async () => {
    // Arrange
    vi.spyOn(SettingsService, "getSettings").mockResolvedValue(
      buildSettings({
        agent_settings: {
          mcp_config: { sse_servers: [], stdio_servers: [], shttp_servers: [] },
        },
      }),
    );
    vi.spyOn(SettingsService, "saveSettings").mockResolvedValue(true);
    const steps: string[] = [];
    const onStep = (event: Event) =>
      steps.push((event as CustomEvent<{ id: string }>).detail.id);
    window.addEventListener(SUPER_ADMIN_SETUP_STEP_EVENT, onStep);
    renderMcpSettingsScreen();
    await screen.findByText("SETTINGS$MCP_NO_SERVERS");

    // Act
    await userEvent.click(
      screen.getByRole("button", { name: "SETTINGS$MCP_ADD_SERVER" }),
    );
    await userEvent.type(
      await screen.findByTestId("url-input"),
      "https://mcp.example.com/sse",
    );
    await userEvent.click(screen.getByTestId("submit-button"));

    // Assert
    await waitFor(() => expect(steps).toEqual(["add-integration"]));
    window.removeEventListener(SUPER_ADMIN_SETUP_STEP_EVENT, onStep);
  });
});

describe("MCPSettingsScreen connection test", () => {
  function renderWithEmptyMcpConfig() {
    vi.spyOn(SettingsService, "getSettings").mockResolvedValue(
      buildSettings({
        mcp_config: { sse_servers: [], stdio_servers: [], shttp_servers: [] },
        agent_settings: {
          mcp_config: { sse_servers: [], stdio_servers: [], shttp_servers: [] },
        },
      }),
    );
    return vi.spyOn(SettingsService, "saveSettings").mockResolvedValue(true);
  }

  async function openAddModalAndTest(url: string) {
    renderMcpSettingsScreen();
    await screen.findByText("SETTINGS$MCP_NO_SERVERS");
    await userEvent.click(
      screen.getByRole("button", { name: "SETTINGS$MCP_ADD_SERVER" }),
    );
    await userEvent.type(await screen.findByTestId("url-input"), url);
    await userEvent.click(screen.getByTestId("mcp-test-connection"));
  }

  it("shows the connection result inline without saving the server", async () => {
    // Arrange
    const saveSettingsSpy = renderWithEmptyMcpConfig();
    const testSpy = vi
      .spyOn(McpService, "testServer")
      .mockResolvedValue({ ok: true, tools: ["search", "fetch"] });

    // Act
    await openAddModalAndTest("https://mcp.example.com/sse");

    // Assert
    expect(await screen.findByTestId("mcp-test-message")).toHaveTextContent(
      "SETTINGS$MCP_TEST_SUCCESS",
    );
    expect(testSpy).toHaveBeenCalledWith({
      name: "sse",
      server: { url: "https://mcp.example.com/sse", transport: "sse" },
    });
    expect(saveSettingsSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId("add-mcp-server-modal")).toBeInTheDocument();
  });

  it.each([
    ["connection", "SETTINGS$MCP_TEST_ERROR_CONNECTION"],
    ["timeout", "SETTINGS$MCP_TEST_ERROR_TIMEOUT"],
    ["unknown", "SETTINGS$MCP_TEST_ERROR_UNKNOWN"],
  ] as const)(
    "explains a failed connection test of kind %s in plain language",
    async (errorKind, expectedMessage) => {
      renderWithEmptyMcpConfig();
      vi.spyOn(McpService, "testServer").mockResolvedValue({
        ok: false,
        error: "probe failed",
        error_kind: errorKind,
      });

      await openAddModalAndTest("https://mcp.example.com/sse");

      expect(await screen.findByTestId("mcp-test-message")).toHaveTextContent(
        expectedMessage,
      );
    },
  );
});

describe("clientLoader permission checks", () => {
  it("should export a clientLoader for route protection", () => {
    expect(clientLoader).toBeDefined();
    expect(typeof clientLoader).toBe("function");
  });
});

describe("MCPSettingsScreen organization MCP servers", () => {
  // A team org of the mock user: org "1" is their Personal Workspace, which
  // has no organization listings.
  const TEAM_ORG_ID = "2";
  const ORG_LISTINGS: OrgMcpServerItem[] = [
    {
      name: "datadog-shared",
      managed_by: "org",
      server: {
        url: "https://dd.example/mcp",
        transport: "http",
        auth: { strategy: "bearer", value: REDACTED_MCP_SECRET },
      },
      secret_names: [],
    },
    {
      name: "bifrost-internal",
      managed_by: "user",
      server: {
        url: "https://bifrost.example/mcp",
        transport: "http",
        auth: { strategy: "bearer", value: REDACTED_MCP_SECRET },
      },
      secret_names: ["BIFROST_TOKEN"],
    },
  ];

  function emptyPersonalSettings() {
    return buildSettings({
      agent_settings: {
        mcp_config: { sse_servers: [], stdio_servers: [], shttp_servers: [] },
      },
    });
  }

  /** Flag on, SaaS mode, the given role, listings and existing secrets. */
  function arrangeOrgMcp({
    role,
    listings = ORG_LISTINGS,
    secrets = [],
  }: {
    role: OrganizationMember["role"];
    listings?: OrgMcpServerItem[];
    secrets?: string[];
  }) {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue({
      app_mode: "saas",
      feature_flags: { enable_org_mcp_servers: true },
    } as never);
    vi.spyOn(SettingsService, "getSettings").mockResolvedValue(
      emptyPersonalSettings(),
    );
    vi.spyOn(organizationService, "getMe").mockResolvedValue({
      org_id: TEAM_ORG_ID,
      user_id: "me",
      email: "me@acme.org",
      role,
      permissions: MOCK_ROLE_PERMISSIONS[role],
      llm_api_key: "",
      max_iterations: 1,
      llm_model: "",
      llm_base_url: "",
      status: "active",
    });
    const getOrgMcpServers = vi
      .spyOn(organizationService, "getOrgMcpServers")
      .mockResolvedValue({ items: listings });
    const secretNames = [...secrets];
    vi.spyOn(SecretsService, "searchSecrets").mockImplementation(async () => ({
      items: secretNames.map((name) => ({ name })),
      next_page_id: null,
    }));
    const createSecret = vi
      .spyOn(SecretsService, "createSecret")
      .mockImplementation(async (name) => {
        secretNames.push(name);
        return true as never;
      });
    return { getOrgMcpServers, createSecret };
  }

  it("keeps today's page when the flag is off", async () => {
    vi.spyOn(SettingsService, "getSettings").mockResolvedValue(
      emptyPersonalSettings(),
    );
    const getOrgMcpServers = vi.spyOn(organizationService, "getOrgMcpServers");

    renderMcpSettingsScreen(TEAM_ORG_ID);

    await screen.findByText("SETTINGS$MCP_NO_SERVERS");
    expect(
      screen.queryByTestId("org-mcp-servers-section"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("mcp-library-section")).not.toBeInTheDocument();
    expect(getOrgMcpServers).not.toHaveBeenCalled();
  });

  it("shows members the organization's listings in two groups with their status", async () => {
    arrangeOrgMcp({ role: "member" });

    renderMcpSettingsScreen(TEAM_ORG_ID);

    const section = await screen.findByTestId("org-mcp-servers-section");
    expect(
      within(section).getByText("SETTINGS$MCP_ORG_SECTION_DESCRIPTION_MEMBER"),
    ).toBeInTheDocument();
    const orgGroup = within(section).getByTestId("org-mcp-server-group-org");
    expect(within(orgGroup).getByText("Datadog Shared")).toBeInTheDocument();
    expect(
      within(orgGroup).getByText("SETTINGS$MCP_ORG_MANAGED_BADGE"),
    ).toBeInTheDocument();
    expect(
      within(orgGroup).queryByTestId("connect-org-mcp-server-button"),
    ).not.toBeInTheDocument();
    const userGroup = within(section).getByTestId("org-mcp-server-group-user");
    expect(
      within(userGroup).getByTestId("org-mcp-server-status"),
    ).toHaveTextContent("SETTINGS$MCP_ORG_NEEDS_CONNECTION");
    expect(
      within(userGroup).getByTestId("connect-org-mcp-server-button"),
    ).toBeInTheDocument();
    // Members never manage the organization's listings.
    expect(
      screen.queryByTestId("add-org-mcp-server-button"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("edit-org-mcp-server-button"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("delete-org-mcp-server-button"),
    ).not.toBeInTheDocument();
  });

  it("shows a stdio listing by its command line and the secret members need", async () => {
    arrangeOrgMcp({
      role: "member",
      listings: [
        {
          name: "slack",
          managed_by: "user",
          server: {
            command: "npx",
            args: ["-y", "@slack/mcp"],
            env: { SLACK_BOT_TOKEN: REDACTED_MCP_SECRET },
          },
          secret_names: ["SLACK_TOKEN"],
        },
      ],
    });

    renderMcpSettingsScreen(TEAM_ORG_ID);

    const row = await screen.findByTestId("org-mcp-server-item");
    expect(
      within(row).getByText("SETTINGS$MCP_SERVER_TYPE_STDIO"),
    ).toBeInTheDocument();
    expect(within(row).getByText("npx -y @slack/mcp")).toBeInTheDocument();
    expect(within(row).getByTestId("org-mcp-server-status")).toHaveTextContent(
      "SETTINGS$MCP_ORG_NEEDS_CONNECTION",
    );
  });

  describe("user-managed OAuth listings", () => {
    const OAUTH_LISTING: OrgMcpServerItem = {
      name: "issue-tracker",
      managed_by: "user",
      server: {
        url: "https://issues.example/mcp",
        transport: "http",
        auth: {
          strategy: "oauth2",
          authentication: {
            type: "oauth",
            client_id: "client-1",
            client_secret: REDACTED_MCP_SECRET,
          },
        },
      },
      secret_names: [],
      connected: false,
    };

    function arrangeFlow(popup: unknown = { close: vi.fn(), location: {} }) {
      vi.spyOn(window, "open").mockReturnValue(popup as never);
      const startOAuth = vi.spyOn(McpService, "startOAuth").mockResolvedValue({
        ok: true,
        job_id: "job-1",
        authorization_url: "https://auth.example/authorize",
      });
      vi.spyOn(McpService, "getOAuthStatus").mockResolvedValue({
        ok: true,
        status: "succeeded",
        job_id: "job-1",
      });
      return { startOAuth };
    }

    it("connects a member with their own account and the masked config", async () => {
      const { getOrgMcpServers } = arrangeOrgMcp({
        role: "member",
        listings: [OAUTH_LISTING],
      });
      const { startOAuth } = arrangeFlow();
      const connect = vi
        .spyOn(organizationService, "connectOrgMcpServer")
        .mockImplementation(async () => {
          getOrgMcpServers.mockResolvedValue({
            items: [{ ...OAUTH_LISTING, connected: true }],
          });
          return true;
        });

      renderMcpSettingsScreen(TEAM_ORG_ID);
      expect(
        await screen.findByTestId("org-mcp-server-status"),
      ).toHaveTextContent("SETTINGS$MCP_ORG_OAUTH_NEEDS_CONNECTION");
      await userEvent.click(
        screen.getByTestId("connect-org-mcp-server-button"),
      );

      await waitFor(() =>
        expect(screen.getByTestId("org-mcp-server-status")).toHaveTextContent(
          "SETTINGS$MCP_ORG_CONNECTED",
        ),
      );
      // The backend restores the client secret from the listing.
      expect(startOAuth).toHaveBeenCalledWith({
        name: "issue-tracker",
        server: OAUTH_LISTING.server,
        timeout: 120,
      });
      // The tokens stay on the job: only its id travels.
      expect(connect).toHaveBeenCalledWith(
        TEAM_ORG_ID,
        "issue-tracker",
        "job-1",
      );
      expect(
        screen.queryByTestId("connect-org-mcp-server-modal"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("connect-org-mcp-server-button"),
      ).not.toBeInTheDocument();
    });

    it("explains a blocked sign-in window and stores nothing", async () => {
      arrangeOrgMcp({ role: "member", listings: [OAUTH_LISTING] });
      vi.spyOn(window, "open").mockReturnValue(null);
      const connect = vi.spyOn(organizationService, "connectOrgMcpServer");

      renderMcpSettingsScreen(TEAM_ORG_ID);
      await userEvent.click(
        await screen.findByTestId("connect-org-mcp-server-button"),
      );

      await waitFor(() =>
        expect(
          screen.getByTestId("connect-org-mcp-server-button"),
        ).toBeEnabled(),
      );
      expect(connect).not.toHaveBeenCalled();
    });

    it("lets a connected member disconnect", async () => {
      const { getOrgMcpServers } = arrangeOrgMcp({
        role: "member",
        listings: [{ ...OAUTH_LISTING, connected: true }],
      });
      const disconnect = vi
        .spyOn(organizationService, "disconnectOrgMcpServer")
        .mockImplementation(async () => {
          getOrgMcpServers.mockResolvedValue({ items: [OAUTH_LISTING] });
          return true;
        });

      renderMcpSettingsScreen(TEAM_ORG_ID);
      await userEvent.click(
        await screen.findByTestId("disconnect-org-mcp-server-button"),
      );

      await screen.findByTestId("connect-org-mcp-server-button");
      expect(disconnect).toHaveBeenCalledWith(TEAM_ORG_ID, "issue-tracker");
    });
  });

  it("shows members an empty organization section when there are no listings", async () => {
    arrangeOrgMcp({ role: "member", listings: [] });

    renderMcpSettingsScreen(TEAM_ORG_ID);

    const section = await screen.findByTestId("org-mcp-servers-section");
    expect(
      within(section).getByText("SETTINGS$MCP_ORG_NONE"),
    ).toBeInTheDocument();
  });

  it("connects a user-managed listing by saving the personal secret it names", async () => {
    const { createSecret } = arrangeOrgMcp({ role: "member" });

    renderMcpSettingsScreen(TEAM_ORG_ID);
    await userEvent.click(
      await screen.findByTestId("connect-org-mcp-server-button"),
    );

    const modal = await screen.findByTestId("connect-org-mcp-server-modal");
    const nameInput = within(modal).getByTestId("name-input");
    expect(nameInput).toHaveValue("BIFROST_TOKEN");
    expect(nameInput).toHaveAttribute("readonly");
    await userEvent.type(within(modal).getByTestId("value-input"), "my-token");
    await userEvent.click(within(modal).getByTestId("submit-button"));

    await waitFor(() =>
      expect(createSecret).toHaveBeenCalledWith(
        "BIFROST_TOKEN",
        "my-token",
        undefined,
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("org-mcp-server-status")).toHaveTextContent(
        "SETTINGS$MCP_ORG_CONNECTED",
      ),
    );
    expect(
      screen.queryByTestId("connect-org-mcp-server-button"),
    ).not.toBeInTheDocument();
  });

  it("lets admins add a user-managed listing", async () => {
    arrangeOrgMcp({ role: "admin", listings: [] });
    const createOrgMcpServer = vi
      .spyOn(organizationService, "createOrgMcpServer")
      .mockResolvedValue(true);

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const section = await screen.findByTestId("org-mcp-servers-section");
    expect(
      within(section).getByText("SETTINGS$MCP_ORG_NONE"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("add-org-mcp-server-button"));

    const modal = await screen.findByTestId("add-org-mcp-server-modal");
    await userEvent.type(
      within(modal).getByTestId("org-name-input"),
      "bifrost",
    );
    await userEvent.type(
      within(modal).getByTestId("org-url-input"),
      "https://bifrost.example/mcp",
    );
    await userEvent.type(
      within(modal).getByTestId("org-secret-name-input"),
      "BIFROST_TOKEN",
    );
    await userEvent.click(within(modal).getByTestId("org-submit-button"));

    await waitFor(() =>
      expect(createOrgMcpServer).toHaveBeenCalledWith(TEAM_ORG_ID, {
        name: "bifrost",
        managed_by: "user",
        server: {
          url: "https://bifrost.example/mcp",
          transport: "http",
          auth: { strategy: "bearer", value: secretReference("BIFROST_TOKEN") },
        },
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByTestId("add-org-mcp-server-modal"),
      ).not.toBeInTheDocument(),
    );
  });

  it("lets admins delete a listing after confirming", async () => {
    arrangeOrgMcp({ role: "admin" });
    const deleteOrgMcpServer = vi
      .spyOn(organizationService, "deleteOrgMcpServer")
      .mockResolvedValue(true);

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const orgGroup = await screen.findByTestId("org-mcp-server-group-org");
    await userEvent.click(
      within(orgGroup).getByTestId("delete-org-mcp-server-button"),
    );
    expect(
      screen.getByText("SETTINGS$MCP_ORG_CONFIRM_DELETE"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("confirm-button"));

    await waitFor(() =>
      expect(deleteOrgMcpServer).toHaveBeenCalledWith(
        TEAM_ORG_ID,
        "datadog-shared",
      ),
    );
  });

  it("keeps a personal server from taking an organization listing's name", async () => {
    arrangeOrgMcp({ role: "member" });
    const saveSettings = vi
      .spyOn(SettingsService, "saveSettings")
      .mockResolvedValue(true);

    renderMcpSettingsScreen(TEAM_ORG_ID);
    await screen.findByTestId("org-mcp-servers-section");
    await userEvent.click(
      screen.getByRole("button", { name: "SETTINGS$MCP_ADD_SERVER" }),
    );
    const modal = await screen.findByTestId("add-mcp-server-modal");
    // The server type dropdown is a combobox; pick STDIO so the name field shows.
    const typeInput = within(modal).getByTestId("server-type-dropdown");
    await userEvent.click(typeInput);
    await userEvent.click(
      await screen.findByRole("option", {
        name: "SETTINGS$MCP_SERVER_TYPE_STDIO",
      }),
    );
    await userEvent.type(
      within(modal).getByTestId("name-input"),
      "bifrost-internal",
    );
    await userEvent.type(within(modal).getByTestId("command-input"), "npx");
    await userEvent.click(within(modal).getByTestId("submit-button"));

    expect(
      await within(modal).findByText("SETTINGS$MCP_ERROR_NAME_RESERVED_BY_ORG"),
    ).toBeInTheDocument();
    expect(saveSettings).not.toHaveBeenCalled();
  });

  it("titles the member's own servers as personal next to the organization's", async () => {
    arrangeOrgMcp({ role: "member" });

    // Org "2" is a team org of the mock user; "1" is their Personal Workspace.
    renderMcpSettingsScreen("2");

    expect(
      await screen.findByRole("heading", {
        name: "SETTINGS$MCP_PERSONAL_SECTION_TITLE",
      }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("settings-page-subtitle")).toHaveTextContent(
      "SETTINGS$MCP_PERSONAL_SECTION_DESCRIPTION",
    );
  });

  it("keeps the Model Context Protocol heading when organization servers are off", async () => {
    vi.spyOn(SettingsService, "getSettings").mockResolvedValue(
      emptyPersonalSettings(),
    );

    renderMcpSettingsScreen();

    expect(
      await screen.findByRole("heading", { name: "SETTINGS$MCP_TITLE" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("settings-page-subtitle")).toHaveTextContent(
      "SETTINGS$MCP_DESCRIPTION",
    );
  });
});

describe("MCPSettingsScreen MCP Library", () => {
  // A team org of the mock user: org "1" is their Personal Workspace.
  const TEAM_ORG_ID = "2";

  /**
   * Flag on, SaaS mode, the given role with no MCP servers of their own.
   * Passing `authProviders` turns OAuth v2 login on with those git providers
   * enabled under Auth Providers.
   */
  function arrangeLibrary({
    role = "member",
    listings = [],
    providersConfigured = [],
    authProviders,
    providerTokensSet = {},
  }: {
    role?: OrganizationMember["role"];
    listings?: OrgMcpServerItem[];
    providersConfigured?: string[];
    authProviders?: string[];
    providerTokensSet?: Settings["provider_tokens_set"];
  } = {}) {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue({
      app_mode: "saas",
      providers_configured: providersConfigured,
      feature_flags: {
        enable_org_mcp_servers: true,
        enable_oauth_v2_login: authProviders !== undefined,
      },
    } as never);
    vi.spyOn(SettingsService, "getSettings").mockResolvedValue(
      buildSettings({
        provider_tokens_set: providerTokensSet,
        agent_settings: {
          mcp_config: { sse_servers: [], stdio_servers: [], shttp_servers: [] },
        },
      }),
    );
    vi.spyOn(organizationService, "getMe").mockResolvedValue({
      org_id: TEAM_ORG_ID,
      user_id: "me",
      email: "me@acme.org",
      role,
      permissions: MOCK_ROLE_PERMISSIONS[role],
      llm_api_key: "",
      max_iterations: 1,
      llm_model: "",
      llm_base_url: "",
      status: "active",
    });
    vi.spyOn(organizationService, "getOrgMcpServers").mockResolvedValue({
      items: listings,
    });
    return {
      saveSettings: vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true),
      testServer: vi
        .spyOn(McpService, "testServer")
        .mockResolvedValue({ ok: true, tools: ["probe"] }),
      createSecret: vi
        .spyOn(SecretsService, "createSecret")
        .mockResolvedValue(true as never),
      listGitProviders: vi
        .spyOn(oauthV2Service, "listGitProviders")
        .mockResolvedValue(
          (authProviders ?? []).map((providerType, index) => ({
            id: index + 1,
            title: providerType,
            icon: null,
            color: "#000000",
            provider_type: providerType,
            connected: false,
            needs_reconnect: false,
          })),
        ),
    };
  }

  async function openLibraryEntry(id: string) {
    await userEvent.type(await screen.findByTestId("mcp-library-search"), id);
    await userEvent.click(screen.getByTestId(`mcp-library-card-${id}`));
    return screen.findByTestId("mcp-install-modal");
  }

  async function savedMcpConfig(saveSettings: {
    mock: { calls: unknown[][] };
  }) {
    await waitFor(() => expect(saveSettings.mock.calls.length).toBe(1));
    const [payload] = saveSettings.mock.calls[0] as [
      { agent_settings_diff: { mcp_config: Record<string, unknown> } },
    ];
    return payload.agent_settings_diff.mcp_config;
  }

  it("lists the catalog's MCP servers in a searchable Library", async () => {
    arrangeLibrary();

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const section = await screen.findByTestId("mcp-library-section");
    await userEvent.type(
      within(section).getByTestId("mcp-library-search"),
      "linear",
    );

    expect(
      within(section)
        .getAllByTestId(/^mcp-library-card-/)
        .map((card) => card.dataset.testid),
    ).toEqual(["mcp-library-card-linear"]);
  });

  it("tests a bearer-token server, then saves it as the member's own without taking an organization listing's name", async () => {
    const { saveSettings, testServer } = arrangeLibrary({
      listings: [
        {
          name: "linear",
          managed_by: "org",
          server: { url: "https://linear.example/mcp", transport: "http" },
          secret_names: [],
        },
      ],
    });

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const modal = await openLibraryEntry("linear");
    await userEvent.type(
      within(modal).getByTestId("mcp-install-field-api_key"),
      "lin_api_key",
    );
    await userEvent.click(within(modal).getByTestId("mcp-install-submit"));

    const server = {
      url: "https://mcp.linear.app/mcp",
      auth: { strategy: "bearer", value: "lin_api_key" },
    };
    expect(testServer).toHaveBeenCalledWith({ name: "linear", server });
    expect(await savedMcpConfig(saveSettings)).toEqual({ linear_1: server });
  });

  it("keeps the install dialog open and saves nothing when the connection test fails", async () => {
    const { saveSettings, testServer } = arrangeLibrary();
    testServer.mockResolvedValue({
      ok: false,
      error: "401 Unauthorized",
      error_kind: "connection",
    });

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const modal = await openLibraryEntry("linear");
    await userEvent.type(
      within(modal).getByTestId("mcp-install-field-api_key"),
      "wrong-key",
    );
    await userEvent.click(within(modal).getByTestId("mcp-install-submit"));

    expect(
      await within(modal).findByTestId("mcp-install-modal-error"),
    ).toHaveTextContent("MCP$TEST_ERROR_CONNECTION");
    expect(saveSettings).not.toHaveBeenCalled();
  });

  it("saves header-field servers with header authentication", async () => {
    const { saveSettings } = arrangeLibrary();

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const modal = await openLibraryEntry("datadog");
    await userEvent.type(
      within(modal).getByTestId("mcp-install-field-DD-API-KEY"),
      "dd-api",
    );
    await userEvent.type(
      within(modal).getByTestId("mcp-install-field-DD-APPLICATION-KEY"),
      "dd-app",
    );
    await userEvent.click(within(modal).getByTestId("mcp-install-submit"));

    expect(await savedMcpConfig(saveSettings)).toEqual({
      datadog: {
        url: "https://mcp.datadoghq.com/v1/mcp",
        auth: {
          strategy: "header",
          headers: { "DD-API-KEY": "dd-api", "DD-APPLICATION-KEY": "dd-app" },
        },
      },
    });
  });

  it("saves a local stdio server without a connection test and keeps its key as a personal secret", async () => {
    const { saveSettings, testServer, createSecret } = arrangeLibrary();

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const modal = await openLibraryEntry("tavily");
    await userEvent.type(
      within(modal).getByTestId("mcp-install-field-TAVILY_API_KEY"),
      "tvly-key",
    );
    await userEvent.click(within(modal).getByTestId("mcp-install-submit"));

    expect(await savedMcpConfig(saveSettings)).toEqual({
      tavily: {
        command: "npx",
        args: ["-y", "tavily-mcp"],
        env: { TAVILY_API_KEY: "tvly-key" },
      },
    });
    expect(testServer).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(createSecret).toHaveBeenCalledWith(
        "TAVILY_API_KEY",
        "tvly-key",
        expect.any(String),
      ),
    );
  });

  it("authorizes an OAuth server in a sign-in window and saves its OAuth state", async () => {
    const { saveSettings, testServer } = arrangeLibrary();
    const signInWindow = { close: vi.fn(), location: { href: "" } };
    vi.spyOn(window, "open").mockReturnValue(signInWindow as never);
    const startOAuth = vi.spyOn(McpService, "startOAuth").mockResolvedValue({
      ok: true,
      job_id: "job-1",
      authorization_url: "https://mcp.miro.com/authorize",
    });
    vi.spyOn(McpService, "getOAuthStatus").mockResolvedValue({
      ok: true,
      status: "succeeded",
      job_id: "job-1",
      oauth_state: { tokens: { access_token: "token" } },
    });

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const modal = await openLibraryEntry("miro");
    await userEvent.click(within(modal).getByTestId("mcp-install-submit"));

    expect(await savedMcpConfig(saveSettings)).toEqual({
      miro: {
        url: "https://mcp.miro.com/",
        auth: {
          strategy: "oauth2",
          state: { tokens: { access_token: "token" } },
        },
      },
    });
    expect(startOAuth).toHaveBeenCalledWith({
      name: "miro",
      server: { url: "https://mcp.miro.com/", auth: { strategy: "oauth2" } },
      timeout: 120,
    });
    expect(testServer).not.toHaveBeenCalled();
  });

  it("offers the instance's native GitHub integration next to the GitHub MCP server", async () => {
    arrangeLibrary({ providersConfigured: ["github"] });
    const open = vi.spyOn(window, "open").mockReturnValue(null);

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const modal = await openLibraryEntry("github");
    await userEvent.click(within(modal).getByTestId("mcp-native-install"));

    expect(
      within(modal).getByTestId("mcp-install-tab-mcp"),
    ).toBeInTheDocument();
    expect(open).toHaveBeenCalledWith(
      `/settings/integrations?org=${TEAM_ORG_ID}`,
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("offers the built-in integration for a provider enabled under Auth Providers when OAuth v2 login is on", async () => {
    arrangeLibrary({ authProviders: ["github"] });

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const modal = await openLibraryEntry("github");

    expect(
      await within(modal).findByTestId("mcp-native-panel"),
    ).toBeInTheDocument();
  });

  it("opens the MCP form directly for a provider that is not enabled under Auth Providers when OAuth v2 login is on", async () => {
    arrangeLibrary({
      providersConfigured: ["gitlab"],
      authProviders: ["github"],
    });

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const modal = await openLibraryEntry("gitlab");

    expect(
      await within(modal).findByTestId("mcp-install-submit"),
    ).toBeInTheDocument();
    expect(
      within(modal).queryByTestId("mcp-native-panel"),
    ).not.toBeInTheDocument();
  });

  it("holds the MCP form back while the Auth Providers list is still loading", async () => {
    const { listGitProviders } = arrangeLibrary({ authProviders: ["github"] });
    listGitProviders.mockReturnValue(new Promise(() => {}));

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const modal = await openLibraryEntry("github");

    expect(
      within(modal).getByTestId("mcp-install-resolving"),
    ).toBeInTheDocument();
    expect(
      within(modal).queryByTestId("mcp-install-submit"),
    ).not.toBeInTheDocument();
  });

  it("takes the member to Settings > Integrations when they continue with a provider that is already connected", async () => {
    arrangeLibrary({
      providersConfigured: ["github"],
      providerTokensSet: { github: "github.com" },
    });

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const modal = await openLibraryEntry("github");
    await userEvent.click(
      await within(modal).findByTestId("mcp-native-continue"),
    );

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/settings/integrations",
    );
  });

  it("lets admins add a Library server to the organization through the prefilled organization form", async () => {
    const { saveSettings, createSecret } = arrangeLibrary({ role: "admin" });
    const createOrgMcpServer = vi
      .spyOn(organizationService, "createOrgMcpServer")
      .mockResolvedValue(true);

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const modal = await openLibraryEntry("github");
    await userEvent.selectOptions(
      within(modal).getByTestId("mcp-install-scope-select"),
      "org",
    );
    expect(
      within(modal).queryByTestId("mcp-install-field-api_key"),
    ).not.toBeInTheDocument();
    expect(
      within(modal).queryAllByTestId(/^mcp-install-save-secret-/),
    ).toHaveLength(0);
    await userEvent.click(within(modal).getByTestId("mcp-install-submit"));

    const orgModal = await screen.findByTestId("add-org-mcp-server-modal");
    expect(screen.queryByTestId("mcp-install-modal")).not.toBeInTheDocument();
    expect(within(orgModal).getByTestId("org-name-input")).toHaveValue(
      "github",
    );
    expect(within(orgModal).getByTestId("org-url-input")).toHaveValue(
      "https://api.githubcopilot.com/mcp/",
    );
    await userEvent.type(
      within(orgModal).getByTestId("org-secret-name-input"),
      "GITHUB_PAT",
    );
    await userEvent.click(within(orgModal).getByTestId("org-submit-button"));

    await waitFor(() =>
      expect(createOrgMcpServer).toHaveBeenCalledWith(TEAM_ORG_ID, {
        name: "github",
        managed_by: "user",
        server: {
          url: "https://api.githubcopilot.com/mcp/",
          transport: "http",
          auth: { strategy: "bearer", value: secretReference("GITHUB_PAT") },
        },
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByTestId("add-org-mcp-server-modal"),
      ).not.toBeInTheDocument(),
    );
    expect(saveSettings).not.toHaveBeenCalled();
    expect(createSecret).not.toHaveBeenCalled();
  });

  it("offers the organization scope for local (stdio) connection methods too", async () => {
    arrangeLibrary({ role: "admin" });

    renderMcpSettingsScreen(TEAM_ORG_ID);
    // Sentry: a remote OAuth option and a local stdio one.
    const sentry = await openLibraryEntry("sentry");
    await userEvent.selectOptions(
      within(sentry).getByTestId("mcp-install-scope-select"),
      "org",
    );
    expect(
      within(sentry).getByTestId("mcp-install-option-api"),
    ).toBeInTheDocument();
    await userEvent.click(within(sentry).getByTestId("mcp-install-cancel"));

    // Tavily: stdio only.
    await userEvent.clear(screen.getByTestId("mcp-library-search"));
    const tavily = await openLibraryEntry("tavily");
    await userEvent.selectOptions(
      within(tavily).getByTestId("mcp-install-scope-select"),
      "org",
    );
    await userEvent.click(within(tavily).getByTestId("mcp-install-submit"));

    const orgModal = await screen.findByTestId("add-org-mcp-server-modal");
    expect(within(orgModal).getByTestId("org-name-input")).toHaveValue(
      "tavily",
    );
    expect(within(orgModal).getByTestId("org-command-input")).toHaveValue(
      "npx",
    );
    expect(within(orgModal).getByTestId("org-args-input")).toHaveValue(
      "-y\ntavily-mcp",
    );
    expect(within(orgModal).getByTestId("org-env-input")).toHaveValue(
      `TAVILY_API_KEY=${secretReference("TAVILY_API_KEY")}`,
    );
  });

  it("gives members no scope choice", async () => {
    arrangeLibrary();

    renderMcpSettingsScreen(TEAM_ORG_ID);
    const modal = await openLibraryEntry("github");

    expect(
      within(modal).queryByTestId("mcp-install-scope-select"),
    ).not.toBeInTheDocument();
    expect(
      within(modal).getByTestId("mcp-install-field-api_key"),
    ).toBeInTheDocument();
  });

  it("gives a personal workspace no scope choice, even for its owner", async () => {
    arrangeLibrary({ role: "owner" });

    renderMcpSettingsScreen("1");
    const modal = await openLibraryEntry("github");

    expect(
      within(modal).queryByTestId("mcp-install-scope-select"),
    ).not.toBeInTheDocument();
  });
});
