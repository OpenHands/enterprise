import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IntegrationRow } from "#/components/features/settings/project-management/integration-row";
import OptionService from "#/api/option-service/option-service.api";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { openHands } from "#/api/open-hands-axios";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { MOCK_PERSONAL_ORG, MOCK_TEAM_ORG_ACME } from "#/mocks/org-handlers";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { Organization, OrganizationMember } from "#/types/org";

vi.mock("react-i18next", async () => {
  const actual =
    await vi.importActual<typeof import("react-i18next")>("react-i18next");
  return {
    ...actual,
    useTranslation: () => ({
      t: (key: string) => key,
      i18n: { changeLanguage: vi.fn() },
    }),
  };
});

const createMockMember = (
  overrides: Partial<OrganizationMember> = {},
): OrganizationMember => ({
  org_id: "org-1",
  user_id: "user-1",
  email: "test@example.com",
  role: "member",
  llm_api_key: "",
  max_iterations: 100,
  llm_model: "gpt-4",
  llm_base_url: "",
  status: "active",
  ...overrides,
});

const notFoundError = new AxiosError(
  "Not Found",
  "ERR_BAD_REQUEST",
  undefined,
  undefined,
  // @ts-expect-error - partial response is enough for status checks
  { status: 404, data: {} },
);

/**
 * Mock the backend endpoints the row touches:
 * - workspaces/link (integration status): no existing link
 * - workspaces/status (org-level status): per-test value
 * - workspaces/validate/{name}: 404 -> workspace not configured yet
 */
const mockJiraEndpoints = ({ configured }: { configured: boolean }) => {
  vi.spyOn(openHands, "get").mockImplementation(async (url: string) => {
    if (url.includes("/workspaces/status")) {
      return {
        data: { configured, host: configured ? "acme.atlassian.net" : null },
      };
    }
    if (url.includes("/workspaces/validate")) {
      throw notFoundError;
    }
    return { data: null };
  });
};

const setupUser = (role: OrganizationMember["role"]) => {
  useSelectedOrganizationStore.setState({ organizationId: "org-1" });
  vi.spyOn(organizationService, "getMe").mockResolvedValue(
    createMockMember({ role }),
  );
};

const setupConfig = (jiraOauthEnabled: boolean) => {
  vi.spyOn(OptionService, "getConfig").mockResolvedValue(
    createMockWebClientConfig({
      app_mode: "saas",
      jira_oauth_enabled: jiraOauthEnabled,
    }),
  );
};

const renderJiraRow = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<IntegrationRow platform="jira" platformName="Jira" />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
};

/**
 * Render a row with `org` as the selected organization. The organizations
 * query is seeded so the org type is known from the first render.
 */
const renderRowInOrg = (platform: "jira" | "linear", org: Organization) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(["organizations"], {
    items: [org],
    currentOrgId: org.id,
  });
  useSelectedOrganizationStore.setState({ organizationId: org.id });
  vi.spyOn(organizationService, "getOrganizations").mockResolvedValue({
    items: [org],
    currentOrgId: org.id,
  });
  return render(
    <IntegrationRow platform={platform} platformName={platform} />,
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    },
  );
};

describe("IntegrationRow (Jira Cloud org scoping)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    useSelectedOrganizationStore.setState({ organizationId: null });
  });

  it("shows the connected guidance to a member in email mode when the org connection exists", async () => {
    setupConfig(false);
    setupUser("member");
    mockJiraEndpoints({ configured: true });

    renderJiraRow();

    await waitFor(() => {
      expect(screen.getByTestId("jira-member-guidance")).toHaveTextContent(
        "PROJECT_MANAGEMENT$JIRA_MEMBER_CONFIGURED_PROMPT",
      );
    });
    expect(
      screen.queryByTestId("jira-configure-button"),
    ).not.toBeInTheDocument();
  });

  it("tells a member in email mode to ask an admin when no org connection exists", async () => {
    setupConfig(false);
    setupUser("member");
    mockJiraEndpoints({ configured: false });

    renderJiraRow();

    await waitFor(() => {
      expect(screen.getByTestId("jira-member-guidance")).toHaveTextContent(
        "PROJECT_MANAGEMENT$JIRA_MEMBER_NOT_CONFIGURED_PROMPT",
      );
    });
  });

  it("shows the configure button to an admin in email mode", async () => {
    setupConfig(false);
    setupUser("admin");
    mockJiraEndpoints({ configured: false });

    renderJiraRow();

    await waitFor(() => {
      expect(screen.getByTestId("jira-configure-button")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("jira-member-guidance"),
    ).not.toBeInTheDocument();
  });

  it("keeps the configure button for a member in OAuth mode so they can link", async () => {
    setupConfig(true);
    setupUser("member");
    mockJiraEndpoints({ configured: false });

    renderJiraRow();

    await waitFor(() => {
      expect(screen.getByTestId("jira-configure-button")).toBeInTheDocument();
    });
  });

  it("shows 'ask an admin' instead of the setup form when a member validates an unconfigured workspace", async () => {
    setupConfig(true);
    setupUser("member");
    mockJiraEndpoints({ configured: false });
    const user = userEvent.setup();

    renderJiraRow();

    // The button is disabled until the integration-status query settles.
    await waitFor(() => {
      expect(screen.getByTestId("jira-configure-button")).toBeEnabled();
    });
    await user.click(screen.getByTestId("jira-configure-button"));
    await user.type(
      screen.getByPlaceholderText(
        "PROJECT_MANAGEMENT$JIRA_WORKSPACE_NAME_PLACEHOLDER",
      ),
      "acme.atlassian.net",
    );
    await user.click(screen.getByTestId("connect-button"));

    await waitFor(() => {
      expect(screen.getByTestId("member-ask-admin")).toBeInTheDocument();
    });
    // The admin setup fields never appear and connecting is no longer offered.
    expect(
      screen.queryByPlaceholderText(
        "PROJECT_MANAGEMENT$WEBHOOK_SECRET_PLACEHOLDER",
      ),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("connect-button")).not.toBeInTheDocument();
  });

  it("shows the manual webhook events URL to an admin configuring in email mode", async () => {
    setupConfig(false);
    setupUser("admin");
    mockJiraEndpoints({ configured: false });
    const user = userEvent.setup();

    renderJiraRow();

    // The button is disabled until the integration-status query settles.
    await waitFor(() => {
      expect(screen.getByTestId("jira-configure-button")).toBeEnabled();
    });
    await user.click(screen.getByTestId("jira-configure-button"));
    await user.type(
      screen.getByPlaceholderText(
        "PROJECT_MANAGEMENT$JIRA_WORKSPACE_NAME_PLACEHOLDER",
      ),
      "acme.atlassian.net",
    );
    await user.click(screen.getByTestId("connect-button"));

    await waitFor(() => {
      expect(screen.getByTestId("jira-webhook-url-value")).toHaveTextContent(
        "/integration/jira/events",
      );
    });
  });

  it("marks Jira Cloud as organization-scoped in a team org", async () => {
    // Arrange
    setupConfig(true);
    setupUser("admin");
    mockJiraEndpoints({ configured: false });

    // Act
    renderRowInOrg("jira", MOCK_TEAM_ORG_ACME);

    // Assert
    expect(await screen.findByTestId("org-scope-badge")).toHaveTextContent(
      "COMMON$ORGANIZATION",
    );
  });

  it("marks Jira Cloud as organization-scoped for a member in email mode", async () => {
    // Arrange
    setupConfig(false);
    setupUser("member");
    mockJiraEndpoints({ configured: true });

    // Act
    renderRowInOrg("jira", MOCK_TEAM_ORG_ACME);

    // Assert
    await screen.findByTestId("jira-member-guidance");
    expect(screen.getByTestId("org-scope-badge")).toBeInTheDocument();
  });

  it("does not mark Jira Cloud as organization-scoped in a personal org", async () => {
    // Arrange
    setupConfig(true);
    setupUser("admin");
    mockJiraEndpoints({ configured: false });

    // Act
    renderRowInOrg("jira", MOCK_PERSONAL_ORG);

    // Assert
    await screen.findByTestId("jira-configure-button");
    expect(screen.queryByTestId("org-scope-badge")).not.toBeInTheDocument();
  });

  it("does not mark Linear as organization-scoped", async () => {
    // Arrange
    setupConfig(true);
    setupUser("admin");
    mockJiraEndpoints({ configured: false });

    // Act
    renderRowInOrg("linear", MOCK_TEAM_ORG_ACME);

    // Assert
    await screen.findByTestId("linear-configure-button");
    expect(screen.queryByTestId("org-scope-badge")).not.toBeInTheDocument();
  });
});

/**
 * Mock a Jira Cloud workspace the caller is already linked to (workspaces/link,
 * with the matching org-level workspaces/status) and a successful save
 * (workspaces). Returns the POST spy.
 */
const mockLinkedJiraWorkspace = (status: "active" | "inactive") => {
  vi.spyOn(openHands, "get").mockImplementation(async (url: string) => {
    if (url.includes("/workspaces/status")) {
      return {
        data: { configured: status === "active", host: "acme.atlassian.net" },
      };
    }
    if (url.includes("/workspaces/link")) {
      return {
        data: {
          id: 1,
          keycloak_user_id: "user-1",
          jira_workspace_id: 10,
          status: "active",
          workspace: {
            id: 10,
            name: "acme.atlassian.net",
            jira_cloud_id: "cloud-1",
            status,
            editable: true,
            svc_acc_email: "bot@acme.com",
          },
        },
      };
    }
    return { data: null };
  });
  return vi
    .spyOn(openHands, "post")
    .mockResolvedValue({ data: { success: true, redirect: false } });
};

const findWorkspaceSave = (post: ReturnType<typeof mockLinkedJiraWorkspace>) =>
  post.mock.calls.find(([url]) => url === "/integration/jira/workspaces");

const openEditModal = async (user: ReturnType<typeof userEvent.setup>) => {
  // The button is disabled until the integration-status query settles.
  await waitFor(() => {
    expect(screen.getByTestId("jira-configure-button")).toBeEnabled();
  });
  await user.click(screen.getByTestId("jira-configure-button"));
};

describe("IntegrationRow (Jira Cloud saved workspace)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    useSelectedOrganizationStore.setState({ organizationId: null });
  });

  it("shows Connected and the hostname when the saved workspace is active", async () => {
    // Arrange
    setupConfig(false);
    setupUser("admin");
    mockLinkedJiraWorkspace("active");

    // Act
    renderJiraRow();

    // Assert
    expect(await screen.findByTestId("jira-status-text")).toHaveTextContent(
      "STATUS$CONNECTED",
    );
    expect(screen.getByTestId("jira-workspace-name")).toHaveTextContent(
      "acme.atlassian.net",
    );
    expect(screen.getByTestId("jira-configure-button")).toHaveTextContent(
      "PROJECT_MANAGEMENT$EDIT_BUTTON_LABEL",
    );
  });

  it("shows Paused and an inactive toggle when the saved workspace is inactive", async () => {
    // Arrange
    setupConfig(false);
    setupUser("admin");
    mockLinkedJiraWorkspace("inactive");
    const user = userEvent.setup();

    // Act
    renderJiraRow();

    // Assert
    expect(await screen.findByTestId("jira-status-text")).toHaveTextContent(
      "PROJECT_MANAGEMENT$JIRA_DC_STATUS_INACTIVE",
    );
    await openEditModal(user);
    expect(screen.getByTestId("active-toggle")).not.toBeChecked();
  });

  it("lets an admin update the saved workspace without re-entering secrets", async () => {
    // Arrange
    setupConfig(false);
    setupUser("admin");
    const post = mockLinkedJiraWorkspace("active");
    const user = userEvent.setup();

    // Act
    renderJiraRow();
    await openEditModal(user);

    // Assert: the saved configuration is reflected and Update is available.
    expect(
      screen.getByPlaceholderText(
        "PROJECT_MANAGEMENT$SERVICE_ACCOUNT_EMAIL_PLACEHOLDER",
      ),
    ).toHaveValue("bot@acme.com");
    expect(
      screen.getByPlaceholderText(
        "PROJECT_MANAGEMENT$WEBHOOK_SECRET_SAVED_PLACEHOLDER",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(
        "PROJECT_MANAGEMENT$JIRA_DC_SVC_ACC_API_SAVED_PLACEHOLDER",
      ),
    ).toBeInTheDocument();
    expect(screen.getByTestId("active-toggle")).toBeChecked();
    expect(screen.getByTestId("connect-button")).toBeEnabled();

    // Act: pause the integration without touching the secrets.
    await user.click(screen.getByTestId("active-toggle"));
    await user.click(screen.getByTestId("connect-button"));

    // Assert: the secrets are omitted so the server keeps the stored values.
    await waitFor(() => {
      expect(findWorkspaceSave(post)).toBeDefined();
    });
    expect(findWorkspaceSave(post)?.[1]).toEqual({
      workspace_name: "acme.atlassian.net",
      svc_acc_email: "bot@acme.com",
      is_active: false,
    });
  });

  it("sends only a newly entered token when replacing a saved secret", async () => {
    // Arrange
    setupConfig(false);
    setupUser("admin");
    const post = mockLinkedJiraWorkspace("active");
    const user = userEvent.setup();

    // Act
    renderJiraRow();
    await openEditModal(user);
    await user.type(
      screen.getByPlaceholderText(
        "PROJECT_MANAGEMENT$JIRA_DC_SVC_ACC_API_SAVED_PLACEHOLDER",
      ),
      "new-token",
    );
    await user.click(screen.getByTestId("connect-button"));

    // Assert
    await waitFor(() => {
      expect(findWorkspaceSave(post)).toBeDefined();
    });
    expect(findWorkspaceSave(post)?.[1]).toEqual({
      workspace_name: "acme.atlassian.net",
      svc_acc_email: "bot@acme.com",
      is_active: true,
      svc_acc_api_key: "new-token",
    });
  });
});
