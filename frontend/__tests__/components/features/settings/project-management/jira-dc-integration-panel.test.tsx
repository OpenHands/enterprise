import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { JiraDcIntegrationPanel } from "#/components/features/settings/project-management/jira-dc-integration-panel";
import OptionService from "#/api/option-service/option-service.api";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { openHands } from "#/api/open-hands-axios";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { MOCK_TEAM_ORG_ACME } from "#/mocks/org-handlers";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";

describe("JiraDcIntegrationPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    useSelectedOrganizationStore.setState({ organizationId: null });
  });

  it("marks Jira Data Center as organization-scoped in a team org", async () => {
    // Arrange
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
      createMockWebClientConfig({ app_mode: "saas" }),
    );
    // No Jira DC workspace is linked yet.
    vi.spyOn(openHands, "get").mockResolvedValue({ data: null });

    // Act
    render(<JiraDcIntegrationPanel />, {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    });

    // Assert
    const panel = await screen.findByTestId("jira-dc-panel");
    expect(within(panel).getByTestId("org-scope-badge")).toHaveTextContent(
      "COMMON$ORGANIZATION",
    );
  });
});
