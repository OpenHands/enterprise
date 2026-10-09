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
import { idpService } from "#/api/idp-service/idp-service.api";
import {
  superAdminService,
  type SuperAdminApiAdmin,
} from "#/api/super-admin-service/super-admin-service.api";
import { SuperAdminAdmins } from "#/components/features/super-admin/super-admin-pages";
import translations from "#/i18n/translation.json";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";

const GRACE: SuperAdminApiAdmin = {
  user_id: "7",
  email: "grace@acme.org",
};

// The signed-in Super Admin's own id. Tests that need to act as someone
// other than Grace override this before rendering.
const { currentUserId } = vi.hoisted(() => ({
  currentUserId: { value: "not-grace" as string | undefined },
}));

vi.mock("#/hooks/query/use-me", () => ({
  useMe: () => ({
    isLoading: false,
    data: currentUserId.value ? { user_id: currentUserId.value } : undefined,
  }),
}));

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
    currentUserId.value = "not-grace";
    vi.spyOn(superAdminService, "listSuperAdmins").mockResolvedValue([GRACE]);
    // The Grant Super Admin button mints a sign-up link, same as the Users
    // page's Create Sign-up Link button, so it needs the local password IDP.
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({
        feature_flags: {
          enable_billing: false,
          hide_llm_settings: false,
          enable_jira: false,
          enable_jira_dc: false,
          enable_linear: false,
          hide_users_page: false,
          hide_billing_page: false,
          hide_integrations_page: false,
          enable_onboarding: false,
          enable_integrated_idp: true,
        },
      }),
    );
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

  it("mints an instance-wide, superadmin-locked sign-up link instead of granting by email directly", async () => {
    // Arrange: this reuses the exact same mechanism as the Users page's
    // Create Sign-up Link button (`MintSignupLinkModal`), just with the
    // role locked to `superadmin` and no org picker.
    const user = userEvent.setup();
    const createSignupLinkSpy = vi
      .spyOn(idpService, "createSignupLink")
      .mockResolvedValue({
        url: "https://app.example.com/oauth/idp/invite?token=abc123",
        role: "superadmin",
        expires_at: "2026-01-08T00:00:00Z",
      });
    renderAdminsPage();
    await screen.findByText("grace@acme.org");

    // Act
    await user.click(
      screen.getByRole("button", { name: "SUPER_ADMIN$GRANT_ADMIN" }),
    );
    const modal = await screen.findByTestId("mint-signup-link-modal");
    // The role picker is hidden -- the whole point of this button is to
    // grant super admin, not to offer a choice.
    expect(
      within(modal).queryByTestId("signup-link-role-dropdown"),
    ).not.toBeInTheDocument();
    await user.type(
      within(modal).getByTestId("signup-link-email-input"),
      "ada@acme.org",
    );
    await user.click(within(modal).getByRole("button", { name: /create/i }));

    // Assert
    expect(createSignupLinkSpy).toHaveBeenCalledExactlyOnceWith({
      email: "ada@acme.org",
      role: "superadmin",
      orgId: undefined,
    });
    const resultModal = await screen.findByTestId("signup-link-result");
    expect(within(resultModal).getByText("ada@acme.org")).toBeInTheDocument();
  });

  it("hides the Grant Super Admin button when the local password IDP is off", async () => {
    // Arrange: without the integrated IDP, the minted link could never be
    // accepted -- same rationale as the Users page's Create Sign-up Link.
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig(),
    );
    renderAdminsPage();

    // Assert
    await screen.findByText("grace@acme.org");
    expect(
      screen.queryByRole("button", { name: "SUPER_ADMIN$GRANT_ADMIN" }),
    ).not.toBeInTheDocument();
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

  it("does not let a Super Admin revoke their own access", async () => {
    // Arrange: the signed-in Super Admin *is* Grace.
    currentUserId.value = GRACE.user_id;
    const user = userEvent.setup();
    const revokeSuperAdmin = vi
      .spyOn(superAdminService, "revokeSuperAdmin")
      .mockResolvedValue(GRACE);
    renderAdminsPage();

    // Act
    await user.click(await screen.findByTestId("super-admin-admin-actions-7"));
    const revokeItem = screen.getByTestId("super-admin-admin-revoke-7");

    // Assert
    expect(revokeItem).toBeDisabled();
    await user.click(revokeItem);
    expect(
      screen.queryByTestId("super-admin-revoke-confirm"),
    ).not.toBeInTheDocument();
    expect(revokeSuperAdmin).not.toHaveBeenCalled();
  });
});
