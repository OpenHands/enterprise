import { beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "#/mocks/node";
import { organizationService } from "#/api/organization-service/organization-service.api";
import SettingsService from "#/api/settings-service/settings-service.api";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { useSuspendedOrganizationStore } from "#/stores/suspended-organization-store";

describe("openHands response interceptor", () => {
  beforeEach(() => {
    useSelectedOrganizationStore.setState({ organizationId: "org-active" });
    useSuspendedOrganizationStore.setState({ suspension: null });
  });

  it("does not block the selected organization when switching to a suspended one fails", async () => {
    // Arrange
    server.use(
      http.post("/api/organizations/org-suspended/switch", () =>
        HttpResponse.json(
          { detail: "Organization is suspended" },
          { status: 403 },
        ),
      ),
    );

    // Act
    await expect(
      organizationService.switchOrganization({ orgId: "org-suspended" }),
    ).rejects.toThrow();

    // Assert
    expect(useSuspendedOrganizationStore.getState().suspension).toBeNull();
  });

  it("does not block the selected organization for a 403 that is not a suspension", async () => {
    // Arrange
    server.use(
      http.get("/api/v1/settings", () =>
        HttpResponse.json(
          { detail: "Missing required permission: view_llm_settings" },
          { status: 403 },
        ),
      ),
    );

    // Act
    await expect(SettingsService.getSettings()).rejects.toThrow();

    // Assert
    expect(useSuspendedOrganizationStore.getState().suspension).toBeNull();
  });
});
