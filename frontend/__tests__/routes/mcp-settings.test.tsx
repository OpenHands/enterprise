import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";
import McpService from "#/api/mcp-service/mcp-service.api";
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

function renderMcpSettingsScreen() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });

  useSelectedOrganizationStore.setState({ organizationId: "1" });

  return render(<MCPSettingsScreen />, {
    wrapper: ({ children }) => (
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          {children}
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
      org_id: "1",
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

    renderMcpSettingsScreen();

    await screen.findByText("SETTINGS$MCP_NO_SERVERS");
    expect(
      screen.queryByTestId("org-mcp-servers-section"),
    ).not.toBeInTheDocument();
    expect(getOrgMcpServers).not.toHaveBeenCalled();
  });

  it("shows members the organization's listings in two groups with their status", async () => {
    arrangeOrgMcp({ role: "member" });

    renderMcpSettingsScreen();

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

  it("hides the organization section from members when it is empty", async () => {
    arrangeOrgMcp({ role: "member", listings: [] });

    renderMcpSettingsScreen();

    await screen.findByText("SETTINGS$MCP_NO_SERVERS");
    await waitFor(() =>
      expect(organizationService.getOrgMcpServers).toHaveBeenCalled(),
    );
    expect(
      screen.queryByTestId("org-mcp-servers-section"),
    ).not.toBeInTheDocument();
  });

  it("connects a user-managed listing by saving the personal secret it names", async () => {
    const { createSecret } = arrangeOrgMcp({ role: "member" });

    renderMcpSettingsScreen();
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

    renderMcpSettingsScreen();
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
      expect(createOrgMcpServer).toHaveBeenCalledWith("1", {
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

    renderMcpSettingsScreen();
    const orgGroup = await screen.findByTestId("org-mcp-server-group-org");
    await userEvent.click(
      within(orgGroup).getByTestId("delete-org-mcp-server-button"),
    );
    expect(
      screen.getByText("SETTINGS$MCP_ORG_CONFIRM_DELETE"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("confirm-button"));

    await waitFor(() =>
      expect(deleteOrgMcpServer).toHaveBeenCalledWith("1", "datadog-shared"),
    );
  });

  it("keeps a personal server from taking an organization listing's name", async () => {
    arrangeOrgMcp({ role: "member" });
    const saveSettings = vi
      .spyOn(SettingsService, "saveSettings")
      .mockResolvedValue(true);

    renderMcpSettingsScreen();
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
});
