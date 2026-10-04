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
import {
  getSuperAdminNuxStep,
  resetSuperAdminNux,
  setSuperAdminNuxStep,
} from "#/utils/org/super-admin-nux";

const NEW_ORG = createMockOrganization("new-org", "My Organization", 0);

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

  it("records the finished wizard on the server, then opens the org LLM defaults", async () => {
    // Arrange
    const updateSetupState = vi
      .spyOn(superAdminService, "updateSetupState")
      .mockResolvedValue({
        wizard_pending: false,
        guide_org_id: null,
        guide_dismissed: false,
      });
    renderOrgStep();

    // Act
    await userEvent.click(await screen.findByTestId("sa-nux-org-continue"));

    // Assert
    expect(await screen.findByTestId("org-llm-defaults")).toBeInTheDocument();
    expect(updateSetupState).toHaveBeenCalledWith({ wizard_completed: true });
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
});
