import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { superAdminService } from "#/api/super-admin-service/super-admin-service.api";
import {
  MOCK_PERSONAL_ORG,
  createMockOrganization,
} from "#/mocks/org-handlers";
import SuperAdminInstallOrg from "#/routes/super-admin-install-org";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import {
  getSuperAdminNuxStep,
  resetSuperAdminNux,
  setSuperAdminNuxStep,
} from "#/utils/org/super-admin-nux";

const NEW_ORG = createMockOrganization("new-org", "My Organization", 0);
const FINISHED_SETUP_STATE = {
  wizard_pending: false,
  guide_org_id: NEW_ORG.id,
  guide_dismissed: false,
  guide_steps: null,
};

function renderOrgStep() {
  const RouterStub = createRoutesStub([
    { path: "/install/org", Component: SuperAdminInstallOrg },
    {
      path: "/settings/org-defaults",
      Component: () => <div data-testid="org-llm-defaults" />,
    },
  ]);
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RouterStub initialEntries={["/install/org"]} />
    </QueryClientProvider>,
  );
}

describe("super admin install org step", () => {
  beforeEach(() => {
    resetSuperAdminNux();
    setSuperAdminNuxStep("org");
    useSelectedOrganizationStore.setState({ organizationId: null });
    vi.spyOn(organizationService, "getOrganizations").mockResolvedValue({
      items: [MOCK_PERSONAL_ORG],
      currentOrgId: MOCK_PERSONAL_ORG.id,
    });
    vi.spyOn(organizationService, "createOrganization").mockResolvedValue(
      NEW_ORG,
    );
    vi.spyOn(organizationService, "switchOrganization").mockResolvedValue(
      NEW_ORG,
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("records the finished wizard and the guide's organization on the server, then opens the org LLM defaults", async () => {
    // Arrange
    const updateSetupState = vi
      .spyOn(superAdminService, "updateSetupState")
      .mockResolvedValue(FINISHED_SETUP_STATE);
    renderOrgStep();

    // Act
    await userEvent.click(await screen.findByTestId("sa-nux-org-continue"));

    // Assert
    expect(await screen.findByTestId("org-llm-defaults")).toBeInTheDocument();
    expect(updateSetupState).toHaveBeenCalledWith({
      wizard_completed: true,
      guide_org_id: NEW_ORG.id,
    });
  });

  it("stays on the org step when the finished wizard cannot be recorded", async () => {
    // Arrange
    vi.spyOn(superAdminService, "updateSetupState").mockRejectedValue(
      new Error("Forbidden"),
    );
    renderOrgStep();

    // Act
    await userEvent.click(await screen.findByTestId("sa-nux-org-continue"));

    // Assert
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByTestId("org-llm-defaults")).not.toBeInTheDocument();
    expect(getSuperAdminNuxStep()).toBe("org");
  });

  it("makes the Super Admin the owner of the new organization and opens it", async () => {
    // Arrange
    vi.spyOn(superAdminService, "updateSetupState").mockResolvedValue(
      FINISHED_SETUP_STATE,
    );
    renderOrgStep();

    // Act
    await userEvent.click(await screen.findByTestId("sa-nux-org-continue"));

    // Assert
    expect(await screen.findByTestId("org-llm-defaults")).toBeInTheDocument();
    expect(organizationService.createOrganization).toHaveBeenCalledWith(
      expect.objectContaining({ owner_user_id: MOCK_PERSONAL_ORG.id }),
    );
    expect(organizationService.switchOrganization).toHaveBeenCalledWith({
      orgId: NEW_ORG.id,
    });
    expect(useSelectedOrganizationStore.getState().organizationId).toBe(
      NEW_ORG.id,
    );
  });

  it("renames the organization the Super Admin already belongs to instead of creating another", async () => {
    // Arrange
    const existingOrg = createMockOrganization("team-org", "Enterprise Org", 0);
    vi.spyOn(organizationService, "getOrganizations").mockResolvedValue({
      items: [MOCK_PERSONAL_ORG, existingOrg],
      currentOrgId: existingOrg.id,
    });
    const updateOrganization = vi
      .spyOn(organizationService, "updateOrganization")
      .mockResolvedValue({ ...existingOrg, name: "My Organization" });
    vi.spyOn(superAdminService, "updateSetupState").mockResolvedValue(
      FINISHED_SETUP_STATE,
    );
    renderOrgStep();

    // Act
    await userEvent.click(await screen.findByTestId("sa-nux-org-continue"));

    // Assert
    expect(await screen.findByTestId("org-llm-defaults")).toBeInTheDocument();
    expect(updateOrganization).toHaveBeenCalledWith({
      orgId: existingOrg.id,
      name: "My Organization",
    });
    expect(organizationService.createOrganization).not.toHaveBeenCalled();
    expect(organizationService.switchOrganization).toHaveBeenCalledWith({
      orgId: existingOrg.id,
    });
  });

  it("stays on the org step when the server refuses to switch into the organization", async () => {
    // Arrange
    vi.spyOn(organizationService, "switchOrganization").mockRejectedValue(
      new Error("Forbidden"),
    );
    const updateSetupState = vi.spyOn(superAdminService, "updateSetupState");
    renderOrgStep();

    // Act
    await userEvent.click(await screen.findByTestId("sa-nux-org-continue"));

    // Assert
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByTestId("org-llm-defaults")).not.toBeInTheDocument();
    expect(updateSetupState).not.toHaveBeenCalled();
  });
});
