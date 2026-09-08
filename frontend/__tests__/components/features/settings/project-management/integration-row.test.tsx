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
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { OrganizationMember } from "#/types/org";

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
});
