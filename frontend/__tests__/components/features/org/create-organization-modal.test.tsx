import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import OptionService from "#/api/option-service/option-service.api";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { superAdminService } from "#/api/super-admin-service/super-admin-service.api";
import { CreateOrganizationModal } from "#/components/features/org/create-organization-modal";
import {
  MOCK_PERSONAL_ORG,
  createMockOrganization,
} from "#/mocks/org-handlers";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";

const NEW_ORG = createMockOrganization("new-org", "Acme", 0);

const renderCreateOrganizationModal = (onClose = vi.fn()) =>
  render(
    <CreateOrganizationModal
      contactName="Ada Lovelace"
      contactEmail="ada@acme.org"
      onClose={onClose}
    />,
    {
      wrapper: ({ children }) => (
        <QueryClientProvider
          client={
            new QueryClient({ defaultOptions: { queries: { retry: false } } })
          }
        >
          {children}
        </QueryClientProvider>
      ),
    },
  );

describe("CreateOrganizationModal", () => {
  beforeEach(() => {
    useSelectedOrganizationStore.setState({ organizationId: "current-org" });
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({ app_mode: "saas" }),
    );
    vi.spyOn(organizationService, "getMe").mockResolvedValue({
      org_id: "current-org",
      user_id: "user-ada",
      email: "ada@acme.org",
      role: "owner",
      max_iterations: 20,
      llm_model: "",
      llm_base_url: "",
      llm_api_key: "",
      status: "active",
    });
    vi.spyOn(organizationService, "getOrganizations").mockResolvedValue({
      items: [MOCK_PERSONAL_ORG],
      currentOrgId: MOCK_PERSONAL_ORG.id,
    });
    vi.spyOn(superAdminService, "listUsers").mockResolvedValue([
      {
        user_id: "user-ada",
        email: "ada@acme.org",
        name: "ada",
        memberships: [],
        status: "active",
      },
      {
        user_id: "user-grace",
        email: "grace@acme.org",
        name: "grace",
        memberships: [],
        status: "active",
      },
    ]);
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

  it("makes the current user the owner by default and switches into the new organization", async () => {
    // Arrange
    renderCreateOrganizationModal();
    await screen.findByDisplayValue("ORG$OWNER_ME");

    // Act
    await userEvent.type(
      screen.getByTestId("create-organization-name"),
      "Acme",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "ORG$CREATE_ORGANIZATION" }),
    );

    // Assert
    await waitFor(() =>
      expect(organizationService.switchOrganization).toHaveBeenCalledWith({
        orgId: NEW_ORG.id,
      }),
    );
    expect(organizationService.createOrganization).toHaveBeenCalledWith({
      name: "Acme",
      contact_name: "Ada Lovelace",
      contact_email: "ada@acme.org",
      owner_user_id: "user-ada",
    });
  });

  it("makes the chosen user the owner without switching into the new organization", async () => {
    // Arrange
    const onClose = vi.fn();
    renderCreateOrganizationModal(onClose);
    await screen.findByDisplayValue("ORG$OWNER_ME");

    // Act
    const ownerDropdown = screen.getByTestId("create-organization-owner");
    await userEvent.click(
      within(ownerDropdown).getByTestId("dropdown-trigger"),
    );
    const listbox = await screen.findByRole("listbox");
    await userEvent.click(await within(listbox).findByText("grace@acme.org"));
    await userEvent.type(
      screen.getByTestId("create-organization-name"),
      "Acme",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "ORG$CREATE_ORGANIZATION" }),
    );

    // Assert
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(organizationService.createOrganization).toHaveBeenCalledWith({
      name: "Acme",
      contact_name: "Ada Lovelace",
      contact_email: "ada@acme.org",
      owner_user_id: "user-grace",
    });
    expect(organizationService.switchOrganization).not.toHaveBeenCalled();
  });
});
