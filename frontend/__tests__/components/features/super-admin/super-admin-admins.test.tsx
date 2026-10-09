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
  type SuperAdminApiAdmin,
} from "#/api/super-admin-service/super-admin-service.api";
import { SuperAdminAdmins } from "#/components/features/super-admin/super-admin-pages";
import translations from "#/i18n/translation.json";

const GRACE: SuperAdminApiAdmin = {
  user_id: "7",
  email: "grace@acme.org",
};

function renderAdminsPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const RouterStub = createRoutesStub([
    { path: "/super-admin/admins", Component: SuperAdminAdmins },
  ]);
  render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>
        <RouterStub initialEntries={["/super-admin/admins"]} />
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

async function chooseRevoke(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByTestId("super-admin-admin-actions-7"));
  await user.click(screen.getByTestId("super-admin-admin-revoke-7"));
}

// The confirmation text names the Super Admin through <Trans>, which only
// interpolates with a real i18n instance.
beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    resources: {
      en: {
        translation: {
          SUPER_ADMIN$REVOKE_ADMIN_CONFIRM:
            translations.SUPER_ADMIN$REVOKE_ADMIN_CONFIRM.en,
        },
      },
    },
    interpolation: { escapeValue: false },
  });
});

describe("Super Admin Admins page", () => {
  beforeEach(() => {
    vi.spyOn(superAdminService, "listSuperAdmins").mockResolvedValue([GRACE]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("asks for confirmation naming the Super Admin before revoking them", async () => {
    // Arrange
    const user = userEvent.setup();
    const revokeSuperAdmin = vi
      .spyOn(superAdminService, "revokeSuperAdmin")
      .mockResolvedValue(GRACE);
    renderAdminsPage();

    // Act
    await chooseRevoke(user);

    // Assert
    const dialog = screen.getByTestId("super-admin-revoke-confirm");
    expect(
      within(dialog).getByText("SUPER_ADMIN$REVOKE_ADMIN_TITLE"),
    ).toBeInTheDocument();
    expect(dialog).toHaveTextContent("grace@acme.org");
    expect(revokeSuperAdmin).not.toHaveBeenCalled();
  });

  it("revokes the Super Admin once the revoke is confirmed", async () => {
    // Arrange
    const user = userEvent.setup();
    const revokeSuperAdmin = vi
      .spyOn(superAdminService, "revokeSuperAdmin")
      .mockResolvedValue(GRACE);
    renderAdminsPage();

    // Act
    await chooseRevoke(user);
    await user.click(screen.getByRole("button", { name: "BUTTON$CONFIRM" }));

    // Assert
    await waitFor(() =>
      expect(revokeSuperAdmin).toHaveBeenCalledWith({ userId: "7" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByTestId("super-admin-revoke-confirm"),
      ).not.toBeInTheDocument(),
    );
  });

  it("grants Super Admin to the entered email and lists them", async () => {
    // Arrange
    const user = userEvent.setup();
    const ada: SuperAdminApiAdmin = { user_id: "8", email: "ada@acme.org" };
    vi.spyOn(superAdminService, "listSuperAdmins")
      .mockResolvedValueOnce([GRACE])
      .mockResolvedValue([GRACE, ada]);
    const grantSuperAdmin = vi
      .spyOn(superAdminService, "grantSuperAdmin")
      .mockResolvedValue(ada);
    renderAdminsPage();
    await screen.findByText("grace@acme.org");

    // Act
    await user.click(
      screen.getByRole("button", { name: "SUPER_ADMIN$GRANT_ADMIN" }),
    );
    const dialog = screen.getByTestId("super-admin-grant-form");
    await user.type(within(dialog).getByRole("textbox"), "ada@acme.org");
    await user.click(
      within(dialog).getByRole("button", { name: "SUPER_ADMIN$GRANT_ADMIN" }),
    );

    // Assert
    await waitFor(() =>
      expect(grantSuperAdmin).toHaveBeenCalledWith({ email: "ada@acme.org" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByTestId("super-admin-grant-form"),
      ).not.toBeInTheDocument(),
    );
    expect(await screen.findByText("ada@acme.org")).toBeInTheDocument();
  });

  it("keeps the Super Admin when the revoke is cancelled", async () => {
    // Arrange
    const user = userEvent.setup();
    const revokeSuperAdmin = vi
      .spyOn(superAdminService, "revokeSuperAdmin")
      .mockResolvedValue(GRACE);
    renderAdminsPage();

    // Act
    await chooseRevoke(user);
    await user.click(screen.getByRole("button", { name: "BUTTON$CANCEL" }));

    // Assert
    expect(
      screen.queryByTestId("super-admin-revoke-confirm"),
    ).not.toBeInTheDocument();
    expect(revokeSuperAdmin).not.toHaveBeenCalled();
    expect(screen.getByText("grace@acme.org")).toBeInTheDocument();
  });
});
