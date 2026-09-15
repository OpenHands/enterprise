import type { ReactElement } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  AdminCatalogPage,
  AdminOverviewPage,
  AdminUserRequestsPage,
} from "#/components/features/integrations-hub/admin-pages";
import { AgentConnectionPage } from "#/components/features/integrations-hub/agent-connection-page";
import { AgentRequestsPage } from "#/components/features/integrations-hub/agent-requests-page";
import { IntegrationsHub } from "#/components/features/integrations-hub/integrations-hub";
import { IntegrationsHubStubProvider } from "#/hooks/query/use-integrations-hub-stub";

const mockOrgTypeAndAccess = vi.hoisted(() => ({
  isPersonalOrg: true,
  isTeamOrg: false,
  organizationId: "personal-1",
  selectedOrg: { id: "personal-1", is_personal: true },
  canViewOrgRoutes: false,
}));

vi.mock("#/hooks/use-org-type-and-access", () => ({
  useOrgTypeAndAccess: () => mockOrgTypeAndAccess,
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (
      key: string,
      options?: { name?: string; count?: number; filter?: string },
    ) => {
      if (options?.name) {
        return `${key}:${options.name}`;
      }
      if (options?.count !== undefined) {
        return `${key}:${options.count}`;
      }
      if (options?.filter) {
        return `${key}:${options.filter}`;
      }
      return key;
    },
  }),
}));

vi.mock("#/utils/custom-toast-handlers", () => ({
  displaySuccessToast: vi.fn(),
}));

function renderHubPage(ui: ReactElement) {
  return render(
    <MemoryRouter>
      <IntegrationsHubStubProvider>{ui}</IntegrationsHubStubProvider>
    </MemoryRouter>,
  );
}

describe("IntegrationsHub", () => {
  beforeEach(() => {
    mockOrgTypeAndAccess.isPersonalOrg = true;
    mockOrgTypeAndAccess.isTeamOrg = false;
  });

  it("lists every integration in a personal workspace and hides the request button", () => {
    renderHubPage(<IntegrationsHub />);

    expect(
      screen.getByTestId("integrations-hub-row-slack"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("integrations-hub-row-github"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("integrations-hub-row-linear"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("integrations-hub-row-jira")).toBeInTheDocument();
    expect(
      screen.getByTestId("integrations-hub-row-notion"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("integrations-hub-row-figma"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("request-integration-button"),
    ).not.toBeInTheDocument();
  });

  it("lists only org-approved integrations and shows the request button for a team org", () => {
    mockOrgTypeAndAccess.isPersonalOrg = false;
    mockOrgTypeAndAccess.isTeamOrg = true;

    renderHubPage(<IntegrationsHub />);

    expect(
      screen.getByTestId("integrations-hub-row-slack"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("integrations-hub-row-github"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("integrations-hub-row-linear"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("integrations-hub-row-figma"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("integrations-hub-row-notion"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("request-integration-button"),
    ).toBeInTheDocument();
  });

  it("connects an integration through the integration wizard", async () => {
    const user = userEvent.setup();
    renderHubPage(<IntegrationsHub />);

    const connectGithub = screen.getByTestId("integrations-hub-connect-github");
    expect(connectGithub).toHaveAttribute("role", "switch");
    expect(connectGithub.className).toContain("rounded-full");

    await user.click(connectGithub);
    expect(screen.getByTestId("integration-wizard-modal")).toBeInTheDocument();

    await user.click(screen.getByTestId("wizard-review-tools"));
    const reviewHeader = screen.getByTestId("integration-wizard-header");
    expect(reviewHeader).toBeInTheDocument();
    expect(within(reviewHeader).getByText("GitHub")).toBeInTheDocument();
    expect(
      within(reviewHeader).getByText("Source control"),
    ).toBeInTheDocument();
    await user.click(screen.getByTestId("wizard-create-integration"));
    expect(
      screen.getByTestId("integrations-hub-status-github"),
    ).toHaveTextContent("STATUS$CONNECTED");
  });

  it("filters installed and available cards by search", async () => {
    const user = userEvent.setup();
    renderHubPage(<IntegrationsHub />);

    await user.type(screen.getByTestId("integrations-hub-search"), "figma");

    expect(
      screen.getByTestId("integrations-hub-row-figma"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("integrations-hub-row-github"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("integrations-hub-row-slack"),
    ).not.toBeInTheDocument();
  });
});

describe("AgentRequestsPage", () => {
  beforeEach(() => {
    mockOrgTypeAndAccess.isPersonalOrg = true;
    mockOrgTypeAndAccess.isTeamOrg = false;
  });

  it("approves a pending request and moves it to the approved tab", async () => {
    const user = userEvent.setup();
    renderHubPage(<AgentRequestsPage />);

    expect(
      screen.getByTestId("integrations-hub-approval-apr-1"),
    ).toBeInTheDocument();

    await user.click(
      screen.getAllByRole("button", { name: "INTEGRATIONS_HUB$APPROVE" })[0],
    );
    await user.click(screen.getByTestId("confirm-button"));

    expect(
      screen.queryByTestId("integrations-hub-approval-apr-1"),
    ).not.toBeInTheDocument();

    await user.click(screen.getByTestId("approvals-filter-approved"));
    expect(
      screen.getByTestId("integrations-hub-approval-apr-1"),
    ).toBeInTheDocument();
  });

  it("links each request row to its conversation", () => {
    renderHubPage(<AgentRequestsPage />);

    const chatLink = screen.getByTestId("approval-conversation-apr-1");
    expect(chatLink).toHaveAttribute("href", "/conversations/1");
    expect(chatLink).toHaveAttribute(
      "aria-label",
      "INTEGRATIONS_HUB$OPEN_CONVERSATION",
    );
  });
});

describe("AgentConnectionPage", () => {
  it("reveals a stub API key", async () => {
    const user = userEvent.setup();
    renderHubPage(<AgentConnectionPage />);

    const key = screen.getByTestId("integrations-hub-key-key-1");
    expect(key).toHaveTextContent("ohk_••••d0be");

    await user.click(screen.getByTestId("integrations-hub-key-key-1-reveal"));
    expect(key).toHaveTextContent("ohk_live_4f2a9c81d0be");
    expect(
      screen.getByTestId("permission-profile-row-profile-1"),
    ).toBeInTheDocument();
  });

  it("opens the profile actions menu outside the overflow list", async () => {
    const user = userEvent.setup();
    renderHubPage(<AgentConnectionPage />);

    await user.click(
      screen.getByTestId("permission-profile-menu-trigger-profile-1"),
    );

    const menu = screen.getByTestId("permission-profile-actions-menu");
    const list = screen.getByTestId("saved-permission-profiles-section");
    expect(menu).toBeInTheDocument();
    expect(list.contains(menu)).toBe(false);
    expect(screen.getByTestId("permission-profile-duplicate")).toBeVisible();
  });

  it("opens the new profile modal without a current-permissions source", async () => {
    const user = userEvent.setup();
    renderHubPage(<AgentConnectionPage />);

    await user.click(screen.getByTestId("agent-connection-new-profile"));

    expect(screen.getByTestId("save-permissions-modal")).toBeInTheDocument();
    expect(
      screen.queryByTestId("save-permissions-source-current"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("save-permissions-source-duplicate"),
    ).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByTestId("save-permissions-source-scratch"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("save-permissions-source-profile"),
    ).toBeInTheDocument();
  });

  it("continues from scratch into the permission editor and saves a profile", async () => {
    const user = userEvent.setup();
    renderHubPage(<AgentConnectionPage />);

    await user.click(screen.getByTestId("agent-connection-new-profile"));
    await user.type(
      screen.getByTestId("save-profile-name"),
      "Research assistant",
    );
    await user.click(screen.getByTestId("save-permissions-source-scratch"));
    await user.click(
      screen.getByRole("button", { name: "INTEGRATIONS_HUB$PROFILE_CONTINUE" }),
    );

    expect(
      screen.queryByTestId("save-permissions-modal"),
    ).not.toBeInTheDocument();
    const editor = screen.getByTestId("edit-permission-profile-modal");
    expect(editor).toBeInTheDocument();
    expect(editor).toHaveAttribute("data-profile-id", "draft-profile");
    expect(editor).toHaveAttribute("data-page", "list");
    expect(
      screen.getByTestId("edit-permission-profile-integration-slack"),
    ).toBeInTheDocument();

    await user.click(
      screen.getByTestId("edit-permission-profile-toggle-slack"),
    );
    await user.click(
      screen.getByRole("button", {
        name: "INTEGRATIONS_HUB$PROFILE_EDIT_SAVE",
      }),
    );

    expect(
      screen.queryByTestId("edit-permission-profile-modal"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Research assistant")).toBeInTheDocument();
  });

  it("opens the permission editor from a saved profile menu", async () => {
    const user = userEvent.setup();
    renderHubPage(<AgentConnectionPage />);

    await user.click(
      screen.getByTestId("permission-profile-menu-trigger-profile-1"),
    );
    await user.click(screen.getByTestId("permission-profile-edit"));

    const editor = screen.getByTestId("edit-permission-profile-modal");
    expect(editor).toHaveAttribute("data-profile-id", "profile-1");
    expect(
      screen.getByTestId("edit-permission-profile-integration-github"),
    ).toBeInTheDocument();

    await user.click(
      screen.getAllByLabelText(
        "INTEGRATIONS_HUB$PROFILE_EDIT_VIEW_TOOLS:GitHub",
      )[0],
    );
    expect(editor).toHaveAttribute("data-page", "tools");
    expect(
      screen.getByTestId("edit-permission-profile-tool-github-search_repos"),
    ).toBeInTheDocument();

    await user.click(screen.getByTestId("edit-permission-profile-tools-back"));
    expect(editor).toHaveAttribute("data-page", "list");
  });
});

describe("Admin catalog and user requests", () => {
  beforeEach(() => {
    mockOrgTypeAndAccess.isPersonalOrg = false;
    mockOrgTypeAndAccess.isTeamOrg = true;
  });

  it("renders overview users in the members list style", async () => {
    const user = userEvent.setup();
    renderHubPage(<AdminOverviewPage />);

    const rows = screen.getAllByTestId("admin-overview-user-row");
    expect(rows[0].tagName).toBe("BUTTON");
    expect(rows[0].className).toContain("h-12");
    expect(rows[0].parentElement?.tagName).toBe("LI");

    await user.click(rows[0]);
    expect(screen.getByTestId("admin-overview-user-modal")).toBeInTheDocument();
  });

  it("opens the Hub-style duplicate group modal and drills into an owner", async () => {
    const user = userEvent.setup();
    renderHubPage(<AdminOverviewPage />);

    await user.click(screen.getByTestId("overview-tabs-duplicates"));
    await user.click(screen.getByTestId("admin-overview-duplicate-row"));

    const modal = screen.getByTestId("admin-overview-duplicate-group-modal");
    expect(
      within(modal).getByTestId("integration-provider-icon-github"),
    ).toBeInTheDocument();
    expect(modal).toHaveTextContent("GitHub");
    expect(modal).toHaveTextContent("octocat");
    expect(modal).not.toHaveTextContent("Alex Chen, Jordan Blake");
    expect(
      within(modal).getByText("INTEGRATIONS_HUB$DUPLICATE_OWNERS_LABEL"),
    ).toBeInTheDocument();
    expect(within(modal).getByText("2")).toBeInTheDocument();
    expect(modal).toHaveTextContent("alex.chen@acme.org");

    await user.click(
      screen.getByTestId("admin-overview-duplicate-owner-alex.chen@acme.org"),
    );
    expect(
      screen.queryByTestId("admin-overview-duplicate-group-modal"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("admin-overview-user-modal")).toHaveTextContent(
      "alex.chen@acme.org",
    );
  });

  it("splits the catalog into registered and available sections", () => {
    renderHubPage(<AdminCatalogPage />);

    const registered = screen.getByTestId("admin-catalog-registered");
    const available = screen.getByTestId("admin-catalog-available");
    expect(registered).toHaveTextContent("Slack");
    expect(
      within(registered).queryByTestId("admin-catalog-row-github"),
    ).not.toBeInTheDocument();
    expect(available).toHaveTextContent("GitHub");
    expect(
      within(available).getByTestId("admin-catalog-row-github"),
    ).toBeInTheDocument();
  });

  it("switches the catalog between list and card views", async () => {
    const user = userEvent.setup();
    renderHubPage(<AdminCatalogPage />);

    expect(
      screen.getByTestId("admin-catalog-registered-grid"),
    ).toBeInTheDocument();
    await user.click(screen.getByTestId("admin-catalog-view-toggle"));
    await user.click(screen.getByTestId("admin-catalog-view-list"));
    expect(
      screen.getByTestId("admin-catalog-registered-list"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("admin-catalog-row-slack").className).toContain(
      "hover:bg-[var(--oh-interactive-hover-low)]",
    );
    expect(
      screen.queryByTestId("admin-catalog-registered-grid"),
    ).not.toBeInTheDocument();
    await user.click(screen.getByTestId("admin-catalog-view-toggle"));
    await user.click(screen.getByTestId("admin-catalog-view-cards"));
    expect(
      screen.getByTestId("admin-catalog-registered-grid"),
    ).toBeInTheDocument();
  });

  it("shows auth type as grey text next to the name in list view", async () => {
    const user = userEvent.setup();
    renderHubPage(<AdminCatalogPage />);

    await user.click(screen.getByTestId("admin-catalog-view-toggle"));
    await user.click(screen.getByTestId("admin-catalog-view-list"));

    const slack = screen.getByTestId("admin-catalog-row-slack");
    const github = screen.getByTestId("admin-catalog-row-github");
    const tavily = screen.getByTestId("admin-catalog-row-tavily");
    const filesystem = screen.getByTestId("admin-catalog-row-filesystem");
    expect(github).toHaveTextContent("INTEGRATIONS_HUB$AUTH_OAUTH");
    expect(tavily).toHaveTextContent("INTEGRATIONS_HUB$AUTH_API_KEY");
    expect(filesystem).not.toHaveTextContent("INTEGRATIONS_HUB$AUTH_API_KEY");
    expect(slack).not.toHaveTextContent("INTEGRATIONS_HUB$REGISTERED");
    expect(github).not.toHaveTextContent("INTEGRATIONS_HUB$READY_STATUS");
    expect(tavily).not.toHaveTextContent("INTEGRATIONS_HUB$READY_STATUS");
    expect(github).not.toHaveTextContent("INTEGRATIONS_HUB$OAUTH_STATUS");
    expect(
      screen.getByTestId("admin-catalog-toggle-slack"),
    ).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByTestId("admin-catalog-connect-github"),
    ).toHaveAttribute("aria-checked", "false");
    expect(slack.querySelector(".lucide-chevron-right")).toBeNull();
    expect(github.querySelector(".lucide-chevron-right")).toBeNull();
  });

  it("places auth labels and plus/check toggles on catalog cards", () => {
    renderHubPage(<AdminCatalogPage />);

    const slack = screen.getByTestId("admin-catalog-row-slack");
    const github = screen.getByTestId("admin-catalog-row-github");
    const slackToggle = screen.getByTestId("admin-catalog-toggle-slack");
    const githubToggle = screen.getByTestId("admin-catalog-connect-github");

    expect(slack).toHaveTextContent("INTEGRATIONS_HUB$AUTH_OAUTH");
    expect(github).toHaveTextContent("INTEGRATIONS_HUB$AUTH_OAUTH");
    expect(slackToggle).toHaveAttribute("aria-checked", "true");
    expect(githubToggle).toHaveAttribute("aria-checked", "false");
    expect(slackToggle.className).toContain("rounded-full");
    expect(githubToggle.className).toContain("rounded-full");
  });

  it("shows source hub placeholders on the add custom MCP modal", async () => {
    const user = userEvent.setup();
    renderHubPage(<AdminCatalogPage />);

    await user.click(
      screen.getByRole("button", { name: "INTEGRATIONS_HUB$ADD_MCP_SERVER" }),
    );

    expect(screen.getByTestId("custom-mcp-name")).toHaveAttribute(
      "placeholder",
      "INTEGRATIONS_HUB$CUSTOM_MCP_NAME_PLACEHOLDER",
    );
    expect(screen.getByTestId("custom-mcp-slug")).toHaveAttribute(
      "placeholder",
      "INTEGRATIONS_HUB$CUSTOM_MCP_SLUG_PLACEHOLDER",
    );
    expect(screen.getByTestId("custom-mcp-url")).toHaveAttribute(
      "placeholder",
      "INTEGRATIONS_HUB$CUSTOM_MCP_URL_PLACEHOLDER",
    );
  });

  it("registers a catalog connector from the admin list", async () => {
    const user = userEvent.setup();
    renderHubPage(<AdminCatalogPage />);

    await user.click(screen.getByTestId("admin-catalog-row-github"));
    expect(screen.getByTestId("connector-setup-github")).toBeInTheDocument();
    await user.click(screen.getByTestId("admin-catalog-register-github"));
    expect(
      within(screen.getByTestId("admin-catalog-registered")).getByTestId(
        "admin-catalog-row-github",
      ),
    ).toBeInTheDocument();
    expect(screen.getByTestId("admin-catalog-toggle-github")).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("shows connector setup steps in the Slack catalog modal", async () => {
    const user = userEvent.setup();
    renderHubPage(<AdminCatalogPage />);

    await user.click(screen.getByTestId("admin-catalog-row-slack"));
    const modal = screen.getByTestId("connector-details-modal-slack");
    expect(modal).toHaveTextContent("INTEGRATIONS_HUB$SETUP_CONFIGURE_TITLE");
    expect(modal).toHaveTextContent("INTEGRATIONS_HUB$SETUP_CONNECT_TITLE");
    expect(modal).toHaveTextContent("INTEGRATIONS_HUB$SETUP_INDEX_TITLE");
    expect(modal).toHaveTextContent("post_message");
    expect(
      screen.getByTestId("connector-tools-search-slack"),
    ).toBeInTheDocument();
    expect(modal).toHaveTextContent("INTEGRATIONS_HUB$SETUP_READY");
    expect(
      within(modal).getByTestId("integration-detail-modal-enable-row-slack"),
    ).toBeInTheDocument();
    expect(
      within(modal).getByTestId("integration-detail-modal-toggle-slack"),
    ).toBeChecked();
  });

  it("does not show the enable toggle for an unregistered catalog connector", async () => {
    const user = userEvent.setup();
    renderHubPage(<AdminCatalogPage />);

    await user.click(screen.getByTestId("admin-catalog-row-github"));
    expect(
      screen.getByTestId("connector-details-modal-github"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("integration-detail-modal-enable-row-github"),
    ).not.toBeInTheDocument();
  });

  it("keeps a disabled registered integration in the registered list", async () => {
    const user = userEvent.setup();
    renderHubPage(<AdminCatalogPage />);

    await user.click(screen.getByTestId("admin-catalog-row-slack"));
    await user.click(screen.getByTestId("integration-detail-modal-toggle-slack"));

    expect(
      screen.getByTestId("integration-detail-modal-toggle-slack"),
    ).not.toBeChecked();
    expect(
      screen.getByTestId("connector-details-modal-slack"),
    ).toHaveTextContent("INTEGRATIONS_HUB$DISABLED_STATE");
    expect(
      within(screen.getByTestId("admin-catalog-registered")).getByTestId(
        "admin-catalog-row-slack",
      ),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("admin-catalog-available")).queryByTestId(
        "admin-catalog-row-slack",
      ),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("admin-catalog-toggle-slack")).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("disables a registered catalog card without unregistering it", async () => {
    const user = userEvent.setup();
    renderHubPage(<AdminCatalogPage />);

    const slackToggle = screen.getByTestId("admin-catalog-toggle-slack");
    expect(slackToggle).toHaveAttribute("aria-checked", "true");
    await user.click(slackToggle);

    expect(slackToggle).toHaveAttribute("aria-checked", "false");
    expect(
      within(screen.getByTestId("admin-catalog-registered")).getByTestId(
        "admin-catalog-row-slack",
      ),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("admin-catalog-available")).queryByTestId(
        "admin-catalog-row-slack",
      ),
    ).not.toBeInTheDocument();
  });

  it("shows user request notes in a hovercard", async () => {
    const user = userEvent.setup();
    renderHubPage(<AdminUserRequestsPage />);

    await user.hover(screen.getByTestId("user-request-notes-req-1"));
    expect(
      screen.getByTestId("user-request-notes-req-1-content"),
    ).toHaveTextContent("Need page comments in standup recaps.");
  });

  it("shows a custom request with the submitted form fields", async () => {
    const user = userEvent.setup();
    renderHubPage(<AdminUserRequestsPage />);

    expect(screen.getByText("Acme CRM")).toBeInTheDocument();
    expect(
      screen.getByTestId("integrations-hub-user-request-req-3"),
    ).toHaveTextContent("INTEGRATIONS_HUB$SOURCE_CUSTOM");

    await user.hover(screen.getByTestId("user-request-notes-req-3"));
    const notes = screen.getByTestId("user-request-notes-req-3-content");
    expect(notes).toHaveTextContent(
      "INTEGRATIONS_HUB$REQUEST_CUSTOM_DESCRIPTION",
    );
    expect(notes).toHaveTextContent(
      "Accounts, opportunities, and follow-up tasks for the sales team.",
    );
    expect(notes).toHaveTextContent("https://example.com/docs");
    expect(notes).toHaveTextContent("Need read access to open deals");
  });

  it("dismisses a user request from the admin table", async () => {
    const user = userEvent.setup();
    renderHubPage(<AdminUserRequestsPage />);

    expect(
      screen.getByTestId("integrations-hub-user-request-req-1"),
    ).toBeInTheDocument();
    await user.click(screen.getByTestId("user-request-dismiss-req-1"));
    expect(
      screen.queryByTestId("integrations-hub-user-request-req-1"),
    ).not.toBeInTheDocument();
  });
});
