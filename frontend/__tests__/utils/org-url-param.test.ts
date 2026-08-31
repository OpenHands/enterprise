import { describe, expect, it, vi, beforeEach } from "vitest";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { WebClientFeatureFlags } from "#/api/option-service/option.types";
import { queryClient } from "#/query-client-config";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { OrganizationsQueryData } from "#/types/org";
import { MOCK_PERSONAL_ORG, MOCK_TEAM_ORG_ACME } from "#/mocks/org-handlers";
import { switchOrganizationFromUrl } from "#/utils/org/org-url-param";

vi.mock("#/query-client-config", async () => {
  const { QueryClient } = await import("@tanstack/react-query");
  return { queryClient: new QueryClient() };
});

vi.mock("#/api/organization-service/organization-service.api", () => ({
  organizationService: {
    getOrganizations: vi.fn(),
    switchOrganization: vi.fn(),
  },
}));

const SELECTED_ORG_STORAGE_KEY = "openhands_selected_org";

const organizationsWithCurrent = (
  currentOrgId: string,
): OrganizationsQueryData => ({
  items: [MOCK_PERSONAL_ORG, MOCK_TEAM_ORG_ACME],
  currentOrgId,
});

const hidePersonalWorkspaces = {
  hide_personal_workspaces: true,
} as WebClientFeatureFlags;

describe("switchOrganizationFromUrl", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    localStorage.clear();
    useSelectedOrganizationStore.setState({ organizationId: null });
    vi.mocked(organizationService.getOrganizations).mockResolvedValue(
      organizationsWithCurrent(MOCK_PERSONAL_ORG.id),
    );
    vi.mocked(organizationService.switchOrganization).mockResolvedValue(
      MOCK_TEAM_ORG_ACME,
    );
  });

  it("switches to the requested org and applies it locally when it is not current", async () => {
    // Act
    const handled = await switchOrganizationFromUrl(
      MOCK_TEAM_ORG_ACME.id,
      undefined,
    );

    // Assert
    expect(handled).toBe(true);
    expect(organizationService.switchOrganization).toHaveBeenCalledWith({
      orgId: MOCK_TEAM_ORG_ACME.id,
    });
    expect(
      queryClient.getQueryData<OrganizationsQueryData>(["organizations"])
        ?.currentOrgId,
    ).toBe(MOCK_TEAM_ORG_ACME.id);
    expect(useSelectedOrganizationStore.getState().organizationId).toBe(
      MOCK_TEAM_ORG_ACME.id,
    );
    expect(localStorage.getItem(SELECTED_ORG_STORAGE_KEY)).toBe(
      MOCK_TEAM_ORG_ACME.id,
    );
  });

  it("does not switch when the requested org is already current", async () => {
    // Act
    const handled = await switchOrganizationFromUrl(
      MOCK_PERSONAL_ORG.id,
      undefined,
    );

    // Assert
    expect(handled).toBe(true);
    expect(organizationService.switchOrganization).not.toHaveBeenCalled();
  });

  it("does not switch to an org the user is not a member of", async () => {
    // Act
    const handled = await switchOrganizationFromUrl("unknown-org", undefined);

    // Assert
    expect(handled).toBe(true);
    expect(organizationService.switchOrganization).not.toHaveBeenCalled();
  });

  it("does not switch to the personal workspace when personal workspaces are hidden", async () => {
    // Arrange: user is on a team org in an org-only install
    vi.mocked(organizationService.getOrganizations).mockResolvedValue(
      organizationsWithCurrent(MOCK_TEAM_ORG_ACME.id),
    );

    // Act
    const handled = await switchOrganizationFromUrl(
      MOCK_PERSONAL_ORG.id,
      hidePersonalWorkspaces,
    );

    // Assert
    expect(handled).toBe(true);
    expect(organizationService.switchOrganization).not.toHaveBeenCalled();
  });

  it("leaves the param unhandled when organizations cannot be fetched", async () => {
    // Arrange: e.g. unauthenticated
    vi.mocked(organizationService.getOrganizations).mockRejectedValue(
      new Error("Unauthorized"),
    );

    // Act
    const handled = await switchOrganizationFromUrl(
      MOCK_TEAM_ORG_ACME.id,
      undefined,
    );

    // Assert
    expect(handled).toBe(false);
    expect(organizationService.switchOrganization).not.toHaveBeenCalled();
  });

  it("treats the param as handled without applying anything when the switch fails", async () => {
    // Arrange
    vi.mocked(organizationService.switchOrganization).mockRejectedValue(
      new Error("Forbidden"),
    );

    // Act
    const handled = await switchOrganizationFromUrl(
      MOCK_TEAM_ORG_ACME.id,
      undefined,
    );

    // Assert
    expect(handled).toBe(true);
    expect(
      queryClient.getQueryData<OrganizationsQueryData>(["organizations"])
        ?.currentOrgId,
    ).toBe(MOCK_PERSONAL_ORG.id);
    expect(useSelectedOrganizationStore.getState().organizationId).toBeNull();
  });
});
