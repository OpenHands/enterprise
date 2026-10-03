import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";
import { createRoutesStub } from "react-router";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import OptionService from "#/api/option-service/option-service.api";
import { organizationService } from "#/api/organization-service/organization-service.api";
import {
  superAdminService,
  type SuperAdminApiOrg,
  type SuperAdminApiUser,
} from "#/api/super-admin-service/super-admin-service.api";
import { SuperAdminOrganizations } from "#/components/features/super-admin/super-admin-pages";
import translations from "#/i18n/translation.json";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";

const ACME: SuperAdminApiOrg = {
  id: "2",
  name: "Acme Corp",
  contact_email: "ops@acme.org",
  contact_name: null,
  member_count: 3,
  is_personal: false,
  status: "active",
};

const SAM: SuperAdminApiUser = {
  user_id: "7",
  email: "sam@beta.llc",
  name: "sam",
  memberships: [
    { org_id: "7", org_name: "user_7_org", role: "owner", status: "active" },
  ],
  status: "active",
};

const SAM_PERSONAL_WORKSPACE: SuperAdminApiOrg = {
  id: "7",
  name: "user_7_org",
  contact_email: "sam@beta.llc",
  contact_name: null,
  member_count: 1,
  is_personal: true,
  status: "active",
};

function renderOrganizationsPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const RouterStub = createRoutesStub([
    {
      path: "/super-admin/organizations",
      Component: SuperAdminOrganizations,
    },
  ]);
  render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>
        <RouterStub initialEntries={["/super-admin/organizations"]} />
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

// The confirmation text names the organization through <Trans>, which only
// interpolates with a real i18n instance.
beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    resources: {
      en: {
        translation: {
          ORG$DELETE_ORGANIZATION_WARNING_WITH_NAME:
            translations.ORG$DELETE_ORGANIZATION_WARNING_WITH_NAME.en,
          SUPER_ADMIN$SUSPEND_ORG_CONFIRM:
            translations.SUPER_ADMIN$SUSPEND_ORG_CONFIRM.en,
        },
      },
    },
    interpolation: { escapeValue: false },
  });
});

async function chooseOrgAction(
  user: ReturnType<typeof userEvent.setup>,
  actionTestId: string,
) {
  await user.click(await screen.findByTestId("super-admin-org-actions-2"));
  await user.click(screen.getByTestId(actionTestId));
}

describe("Super Admin Organizations page", () => {
  beforeEach(() => {
    vi.spyOn(superAdminService, "listOrganizations").mockResolvedValue([ACME]);
    vi.spyOn(superAdminService, "listUsers").mockResolvedValue([]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useSelectedOrganizationStore.setState({ organizationId: null });
  });

  it("asks for confirmation naming the organization before deleting it", async () => {
    // Arrange
    const user = userEvent.setup();
    const deleteOrganization = vi
      .spyOn(superAdminService, "deleteOrganization")
      .mockResolvedValue();
    renderOrganizationsPage();

    // Act
    await chooseOrgAction(user, "super-admin-org-remove-2");

    // Assert
    const dialog = screen.getByTestId("super-admin-org-confirm");
    expect(
      within(dialog).getByText("ORG$DELETE_ORGANIZATION"),
    ).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Acme Corp");
    expect(deleteOrganization).not.toHaveBeenCalled();
  });

  it("deletes the organization once the deletion is confirmed", async () => {
    // Arrange
    const user = userEvent.setup();
    const deleteOrganization = vi
      .spyOn(superAdminService, "deleteOrganization")
      .mockResolvedValue();
    renderOrganizationsPage();

    // Act
    await chooseOrgAction(user, "super-admin-org-remove-2");
    await user.click(screen.getByRole("button", { name: "BUTTON$CONFIRM" }));

    // Assert
    await waitFor(() =>
      expect(deleteOrganization).toHaveBeenCalledWith({ orgId: "2" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByTestId("super-admin-org-confirm"),
      ).not.toBeInTheDocument(),
    );
  });

  it("keeps the organization when the deletion is cancelled", async () => {
    // Arrange
    const user = userEvent.setup();
    const deleteOrganization = vi
      .spyOn(superAdminService, "deleteOrganization")
      .mockResolvedValue();
    renderOrganizationsPage();

    // Act
    await chooseOrgAction(user, "super-admin-org-remove-2");
    await user.click(screen.getByRole("button", { name: "BUTTON$CANCEL" }));

    // Assert
    expect(
      screen.queryByTestId("super-admin-org-confirm"),
    ).not.toBeInTheDocument();
    expect(deleteOrganization).not.toHaveBeenCalled();
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
  });

  it("asks for confirmation naming the organization before suspending it", async () => {
    // Arrange
    const user = userEvent.setup();
    const updateOrganizationStatus = vi
      .spyOn(superAdminService, "updateOrganizationStatus")
      .mockResolvedValue({ ...ACME, status: "suspended" });
    renderOrganizationsPage();

    // Act
    await chooseOrgAction(user, "super-admin-org-suspend-2");

    // Assert
    const dialog = screen.getByTestId("super-admin-org-confirm");
    expect(
      within(dialog).getByText("SUPER_ADMIN$SUSPEND_ORG_TITLE"),
    ).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Acme Corp");
    expect(updateOrganizationStatus).not.toHaveBeenCalled();
  });

  it("suspends the organization once the suspension is confirmed", async () => {
    // Arrange
    const user = userEvent.setup();
    const updateOrganizationStatus = vi
      .spyOn(superAdminService, "updateOrganizationStatus")
      .mockResolvedValue({ ...ACME, status: "suspended" });
    renderOrganizationsPage();

    // Act
    await chooseOrgAction(user, "super-admin-org-suspend-2");
    await user.click(screen.getByRole("button", { name: "BUTTON$CONFIRM" }));

    // Assert
    await waitFor(() =>
      expect(updateOrganizationStatus).toHaveBeenCalledWith({
        orgId: "2",
        status: "suspended",
      }),
    );
  });

  it("does not offer to open or act on another user's personal workspace", async () => {
    // Arrange
    vi.spyOn(superAdminService, "listOrganizations").mockResolvedValue([
      SAM_PERSONAL_WORKSPACE,
    ]);
    vi.spyOn(superAdminService, "listUsers").mockResolvedValue([SAM]);

    // Act
    renderOrganizationsPage();

    // Assert
    await screen.findByText("user_7_org");
    await waitFor(() =>
      expect(
        screen.queryByTestId("super-admin-org-open-7"),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByTestId("super-admin-org-actions-7"),
    ).not.toBeInTheDocument();
  });

  it("offers only View on the super admin's own personal workspace", async () => {
    // Arrange
    const user = userEvent.setup();
    vi.spyOn(superAdminService, "listOrganizations").mockResolvedValue([
      SAM_PERSONAL_WORKSPACE,
    ]);
    vi.spyOn(superAdminService, "listUsers").mockResolvedValue([SAM]);
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({ app_mode: "saas" }),
    );
    useSelectedOrganizationStore.setState({ organizationId: "7" });
    vi.spyOn(organizationService, "getMe").mockResolvedValue({
      org_id: "7",
      user_id: "7",
      email: "sam@beta.llc",
      role: "owner",
      llm_api_key: "",
      max_iterations: 100,
      llm_model: "gpt-4",
      llm_base_url: "",
      status: "active",
    });
    renderOrganizationsPage();

    // Act
    await user.click(await screen.findByTestId("super-admin-org-actions-7"));

    // Assert
    expect(screen.getByTestId("super-admin-org-view-7")).toBeInTheDocument();
    expect(
      screen.queryByTestId("super-admin-org-suspend-7"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("super-admin-org-remove-7"),
    ).not.toBeInTheDocument();
  });
});
