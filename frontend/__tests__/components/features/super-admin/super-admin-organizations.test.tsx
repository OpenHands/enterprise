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
import {
  superAdminService,
  type SuperAdminApiOrg,
} from "#/api/super-admin-service/super-admin-service.api";
import { SuperAdminOrganizations } from "#/components/features/super-admin/super-admin-pages";
import translations from "#/i18n/translation.json";

const ACME: SuperAdminApiOrg = {
  id: "2",
  name: "Acme Corp",
  contact_email: "ops@acme.org",
  contact_name: null,
  member_count: 3,
  is_personal: false,
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
});
