import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { JiraDcIntegrationPanel } from "#/components/features/settings/project-management/jira-dc-integration-panel";
import OptionService from "#/api/option-service/option-service.api";
import { WebClientConfig } from "#/api/option-service/option.types";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { openHands } from "#/api/open-hands-axios";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { MOCK_TEAM_ORG_ACME } from "#/mocks/org-handlers";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";

function renderPanelAsTeamOrgAdmin(config: Partial<WebClientConfig> = {}) {
  const org = MOCK_TEAM_ORG_ACME;
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
  vi.spyOn(organizationService, "getMe").mockResolvedValue({
    org_id: org.id,
    user_id: "user-1",
    email: "admin@example.com",
    role: "admin",
    llm_api_key: "",
    max_iterations: 100,
    llm_model: "gpt-4",
    llm_base_url: "",
    status: "active",
  });
  vi.spyOn(OptionService, "getConfig").mockResolvedValue(
    createMockWebClientConfig({ app_mode: "saas", ...config }),
  );
  // No Jira DC workspace is linked yet.
  vi.spyOn(openHands, "get").mockResolvedValue({ data: null });

  render(<JiraDcIntegrationPanel />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
}

describe("JiraDcIntegrationPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    useSelectedOrganizationStore.setState({ organizationId: null });
  });

  it("marks Jira Data Center as organization-scoped in a team org", async () => {
    // Arrange / Act
    renderPanelAsTeamOrgAdmin();

    // Assert
    const panel = await screen.findByTestId("jira-dc-panel");
    expect(within(panel).getByTestId("org-scope-badge")).toHaveTextContent(
      "COMMON$ORGANIZATION",
    );
  });

  it("lets an admin enter the Jira host when the service account is managed in email mode", async () => {
    // Arrange
    const user = userEvent.setup();
    renderPanelAsTeamOrgAdmin({
      jira_dc_oauth_host: null,
      jira_dc_service_account_managed: true,
      jira_dc_service_account_email: "svc@example.com",
    });

    // Act
    await user.click(await screen.findByTestId("jira-dc-configure-button"));
    await user.type(
      await screen.findByTestId("jira-dc-host-input"),
      "jira.example.com",
    );
    await user.type(screen.getByTestId("admin-api-key-input"), "admin-pat");

    // Assert
    expect(screen.queryByTestId("jira-dc-svc-pat-input")).not.toBeInTheDocument();
    expect(screen.getByTestId("jira-dc-submit-button")).toBeEnabled();
  });
});
