import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";
import { createRoutesStub, useParams } from "react-router";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { authProvidersService } from "#/api/auth-providers-service/auth-providers-service.api";
import { licenseService } from "#/api/license-service/license-service.api";
import OptionService from "#/api/option-service/option-service.api";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import { DEFAULT_AUTH_PROVIDER_COLOR } from "#/api/auth-providers-service/auth-providers.types";
import { SuperAdminAuthProviders } from "#/components/features/super-admin/super-admin-auth-providers";
import translations from "#/i18n/translation.json";
import type { AuthProvider } from "#/api/auth-providers-service/auth-providers.types";
import {
  GITHUB_PROVIDER,
  PASSWORD_PROVIDER,
  SAML_PROVIDER,
} from "./auth-provider-fixtures";

function ProviderPathProbe() {
  const { providerId } = useParams<{ providerId: string }>();
  return <p data-testid="location">{providerId}</p>;
}

function renderAuthProvidersPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const RouterStub = createRoutesStub([
    {
      path: "/super-admin/auth-providers",
      Component: SuperAdminAuthProviders,
    },
    {
      path: "/super-admin/auth-providers/:providerId",
      Component: ProviderPathProbe,
    },
    {
      path: "/super-admin/auth-providers/:providerId/users",
      Component: ProviderPathProbe,
    },
  ]);
  render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>
        <RouterStub initialEntries={["/super-admin/auth-providers"]} />
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

async function openCreateModal(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("GitHub");
  await user.click(
    screen.getByRole("button", { name: "SUPER_ADMIN$ADD_AUTH_PROVIDER" }),
  );
  return screen.getByTestId("super-admin-auth-provider-create");
}

async function selectProviderType(
  user: ReturnType<typeof userEvent.setup>,
  dialog: HTMLElement,
  optionName: string,
) {
  await user.click(
    within(dialog).getByTestId("super-admin-auth-provider-type"),
  );
  await user.click(await screen.findByRole("option", { name: optionName }));
}

// The delete confirmation names the provider through i18next interpolation,
// which only resolves `{{name}}` with a real i18n instance.
beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    resources: {
      en: {
        translation: {
          SUPER_ADMIN$DELETE_AUTH_PROVIDER_CONFIRM:
            translations.SUPER_ADMIN$DELETE_AUTH_PROVIDER_CONFIRM.en,
        },
      },
    },
    interpolation: { escapeValue: false },
  });
});

describe("Super Admin Auth Providers page", () => {
  beforeEach(() => {
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      GITHUB_PROVIDER,
    ]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists configured auth providers by title, kind, and an IDP switch that only OAuth rows can flip, without client id/secret columns", async () => {
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      GITHUB_PROVIDER,
      PASSWORD_PROVIDER,
      SAML_PROVIDER,
    ]);
    renderAuthProvidersPage();

    expect(await screen.findByText("GitHub")).toBeInTheDocument();
    expect(
      screen.getByText("SUPER_ADMIN$AUTH_PROVIDER_KIND_OAUTH"),
    ).toBeInTheDocument();

    const githubIsIdpSwitch = screen.getByRole("switch", {
      name: "SUPER_ADMIN$COL_IS_IDP: GitHub",
    });
    expect(githubIsIdpSwitch).toBeEnabled();
    expect(githubIsIdpSwitch).toHaveAttribute("aria-checked", "false");

    const passwordIsIdpSwitch = screen.getByRole("switch", {
      name: "SUPER_ADMIN$COL_IS_IDP: Password Login",
    });
    expect(passwordIsIdpSwitch).toBeDisabled();
    expect(passwordIsIdpSwitch).toHaveAttribute("aria-checked", "true");

    const samlIsIdpSwitch = screen.getByRole("switch", {
      name: "SUPER_ADMIN$COL_IS_IDP: Okta SAML",
    });
    expect(samlIsIdpSwitch).toBeDisabled();
    expect(samlIsIdpSwitch).toHaveAttribute("aria-checked", "true");

    expect(screen.queryByText("github-client-id")).not.toBeInTheDocument();
    expect(
      screen.queryByText("SUPER_ADMIN$OAUTH_PROVIDER_SECRET_SET"),
    ).not.toBeInTheDocument();
  });

  it("shows each provider's color as a swatch in the first column", async () => {
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      { ...GITHUB_PROVIDER, color: "#ff0000" },
      PASSWORD_PROVIDER,
    ]);
    renderAuthProvidersPage();

    const swatch = await screen.findByTestId(
      "super-admin-auth-provider-color-swatch-1",
    );
    expect(swatch).toHaveStyle({ backgroundColor: "rgb(255, 0, 0)" });
    expect(swatch.closest("td")).toBe(swatch.closest("tr")?.firstElementChild);
    expect(
      screen.getByTestId("super-admin-auth-provider-color-swatch-2"),
    ).toHaveStyle({ backgroundColor: DEFAULT_AUTH_PROVIDER_COLOR });
  });

  it("switches an OAuth provider's Login IDP from the table without opening the edit form", async () => {
    // Arrange
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue({ ...GITHUB_PROVIDER, is_idp: true });
    renderAuthProvidersPage();

    // Act
    await user.click(
      await screen.findByRole("switch", {
        name: "SUPER_ADMIN$COL_IS_IDP: GitHub",
      }),
    );

    // Assert
    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledExactlyOnceWith({
        id: 1,
        is_idp: true,
      }),
    );
    expect(
      screen.queryByTestId("super-admin-auth-provider-form"),
    ).not.toBeInTheDocument();
  });

  it("toggles a provider's enabled flag from the table", async () => {
    // Arrange
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue({ ...GITHUB_PROVIDER, enabled: false });
    renderAuthProvidersPage();

    // Act
    await user.click(
      await screen.findByRole("switch", {
        name: "SUPER_ADMIN$COL_ENABLED: GitHub",
      }),
    );

    // Assert
    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledExactlyOnceWith({
        id: 1,
        enabled: false,
      }),
    );
  });

  it("only asks for a provider type when adding a provider", async () => {
    const user = userEvent.setup();
    renderAuthProvidersPage();
    const dialog = await openCreateModal(user);

    expect(
      within(dialog).getByTestId("super-admin-auth-provider-type"),
    ).toBeInTheDocument();
    expect(dialog.querySelectorAll("input, textarea")).toHaveLength(1);
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-create-submit"),
    ).toBeDisabled();

    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-type"),
    );
    const options = (await screen.findAllByRole("option")).map(
      (option) => option.textContent,
    );
    expect(options).toEqual([
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_GOOGLE",
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_OKTA",
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_OAUTH",
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_GITHUB",
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_GITLAB",
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_BITBUCKET",
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_BITBUCKET_DATA_CENTER",
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_AZURE_DEVOPS",
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_SAML",
      "SUPER_ADMIN$AUTH_PROVIDER_KIND_PASSWORD",
    ]);
  });

  it("creates a Google OIDC provider with placeholder values and opens its edit page", async () => {
    const user = userEvent.setup();
    const createAuthProvider = vi
      .spyOn(authProvidersService, "createAuthProvider")
      .mockResolvedValue({ ...GITHUB_PROVIDER, id: 7 });
    renderAuthProvidersPage();
    const dialog = await openCreateModal(user);

    await selectProviderType(
      user,
      dialog,
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_GOOGLE",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-create-submit"),
    );

    await waitFor(() =>
      expect(createAuthProvider).toHaveBeenCalledExactlyOnceWith({
        title: "Log in with Google",
        is_idp: true,
        icon: null,
        enabled: false,
        color: "#1a73e8",
        allow_registration: true,
        allow_match_by_email: true,
        details: {
          kind: "OAuthProviderDetails",
          provider_type: "enterprise_sso",
          client_id: "PLACEHOLDER",
          authorization_url: "https://accounts.google.com/o/oauth2/auth",
          token_url: "https://oauth2.googleapis.com/token",
          userinfo_url: "https://openidconnect.googleapis.com/v1/userinfo",
          scopes: ["openid", "email"],
          permitted_drift_seconds: 60,
          legacy_keycloak_type: null,
          named_secret: null,
          attribute_mapping: {},
        },
      }),
    );
    expect(await screen.findByTestId("location")).toHaveTextContent("7");
  });

  it("creates an Okta provider with placeholder domain URLs", async () => {
    const user = userEvent.setup();
    const createAuthProvider = vi
      .spyOn(authProvidersService, "createAuthProvider")
      .mockResolvedValue({ ...GITHUB_PROVIDER, id: 8 });
    renderAuthProvidersPage();
    const dialog = await openCreateModal(user);

    await selectProviderType(
      user,
      dialog,
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_OKTA",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-create-submit"),
    );

    await waitFor(() =>
      expect(createAuthProvider).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({
          title: "Login with Okta",
          color: "#4c64e1",
          details: expect.objectContaining({
            provider_type: "enterprise_sso",
            client_id: "PLACEHOLDER",
            authorization_url:
              "https://YOUR_DOMAIN.okta.com/oauth2/v1/authorize",
            token_url: "https://YOUR_DOMAIN.okta.com/oauth2/v1/token",
            userinfo_url: "https://YOUR_DOMAIN.okta.com/oauth2/v1/userinfo",
            scopes: ["openid", "email", "offline_access"],
          }),
        }),
      ),
    );
  });

  it("creates a generic OAuth provider with placeholder URLs", async () => {
    const user = userEvent.setup();
    const createAuthProvider = vi
      .spyOn(authProvidersService, "createAuthProvider")
      .mockResolvedValue({ ...GITHUB_PROVIDER, id: 9 });
    renderAuthProvidersPage();
    const dialog = await openCreateModal(user);

    await selectProviderType(
      user,
      dialog,
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_OAUTH",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-create-submit"),
    );

    await waitFor(() =>
      expect(createAuthProvider).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({
          title: "Login with OAuth",
          details: expect.objectContaining({
            client_id: "PLACEHOLDER",
            authorization_url: "https://PLACEHOLDER.COM/OAUTH/auth",
            token_url: "https://PLACEHOLDER.COM/OAUTH/token",
            userinfo_url: "https://PLACEHOLDER.COM/OAUTH/userinfo",
            scopes: ["openid", "email", "offline_access"],
          }),
        }),
      ),
    );
  });

  it("creates a GitHub provider with GitHub's OAuth endpoints and a white icon", async () => {
    const user = userEvent.setup();
    const createAuthProvider = vi
      .spyOn(authProvidersService, "createAuthProvider")
      .mockResolvedValue({ ...GITHUB_PROVIDER, id: 10 });
    renderAuthProvidersPage();
    const dialog = await openCreateModal(user);

    await selectProviderType(
      user,
      dialog,
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_GITHUB",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-create-submit"),
    );

    await waitFor(() => expect(createAuthProvider).toHaveBeenCalledOnce());
    const params = createAuthProvider.mock.calls[0][0];
    expect(params).toEqual(
      expect.objectContaining({
        title: "Log in with GitHub",
        is_idp: true,
        color: "#9E28B0",
        details: expect.objectContaining({
          provider_type: "github",
          client_id: "PLACEHOLDER",
          authorization_url: "https://github.com/login/oauth/authorize",
          token_url: "https://github.com/login/oauth/access_token",
          userinfo_url: "https://api.github.com/user",
          legacy_keycloak_type: null,
          named_secret: "GITHUB_TOKEN",
          attribute_mapping: {},
        }),
      }),
    );
    expect(params.icon).toContain("<svg");
    expect(params.icon).toContain("#FFFFFF");
    expect(params.icon).not.toContain("currentColor");
  });

  it("creates a Bitbucket Data Center provider with placeholder host URLs", async () => {
    const user = userEvent.setup();
    const createAuthProvider = vi
      .spyOn(authProvidersService, "createAuthProvider")
      .mockResolvedValue({ ...GITHUB_PROVIDER, id: 13 });
    renderAuthProvidersPage();
    const dialog = await openCreateModal(user);

    await selectProviderType(
      user,
      dialog,
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_BITBUCKET_DATA_CENTER",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-create-submit"),
    );

    await waitFor(() => expect(createAuthProvider).toHaveBeenCalledOnce());
    const params = createAuthProvider.mock.calls[0][0];
    expect(params).toEqual(
      expect.objectContaining({
        title: "Log in with Bitbucket Data Center",
        is_idp: true,
        enabled: false,
        color: "#0052CC",
        details: expect.objectContaining({
          provider_type: "bitbucket_data_center",
          client_id: "PLACEHOLDER",
          authorization_url:
            "https://YOUR_BITBUCKET_HOST/rest/oauth2/latest/authorize",
          token_url: "https://YOUR_BITBUCKET_HOST/rest/oauth2/latest/token",
          userinfo_url: "https://YOUR_BITBUCKET_HOST/rest/api/1.0/users",
          scopes: ["PROJECT_READ", "REPO_READ", "REPO_WRITE"],
          legacy_keycloak_type: null,
          named_secret: "BITBUCKET_DATA_CENTER_TOKEN",
          attribute_mapping: {},
        }),
      }),
    );
    expect(params.icon).toContain("<svg");
    expect(params.icon).not.toContain("currentColor");
    expect(await screen.findByTestId("location")).toHaveTextContent("13");
  });

  it("creates an Azure DevOps provider that requests a refresh token from Entra", async () => {
    const user = userEvent.setup();
    const createAuthProvider = vi
      .spyOn(authProvidersService, "createAuthProvider")
      .mockResolvedValue({ ...GITHUB_PROVIDER, id: 12 });
    renderAuthProvidersPage();
    const dialog = await openCreateModal(user);

    await selectProviderType(
      user,
      dialog,
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_AZURE_DEVOPS",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-create-submit"),
    );

    await waitFor(() => expect(createAuthProvider).toHaveBeenCalledOnce());
    const params = createAuthProvider.mock.calls[0][0];
    expect(params).toEqual(
      expect.objectContaining({
        title: "Log in with Azure DevOps",
        is_idp: true,
        enabled: false,
        color: "#0078D4",
        details: expect.objectContaining({
          provider_type: "azure_devops",
          client_id: "PLACEHOLDER",
          authorization_url:
            "https://login.microsoftonline.com/YOUR_TENANT_ID/oauth2/v2.0/authorize",
          token_url:
            "https://login.microsoftonline.com/YOUR_TENANT_ID/oauth2/v2.0/token",
          userinfo_url: null,
          scopes: [
            "openid",
            "email",
            "profile",
            "offline_access",
            "499b84ac-1321-427f-aa17-267ca6975798/vso.profile",
            "499b84ac-1321-427f-aa17-267ca6975798/vso.code_write",
            "499b84ac-1321-427f-aa17-267ca6975798/vso.project",
            "499b84ac-1321-427f-aa17-267ca6975798/vso.work_write",
          ],
          legacy_keycloak_type: null,
          named_secret: "AZURE_DEVOPS_TOKEN",
          attribute_mapping: {},
        }),
      }),
    );
    expect(params.icon).toContain("<svg");
    expect(params.icon).not.toContain("currentColor");
    expect(await screen.findByTestId("location")).toHaveTextContent("12");
  });

  it("creates a SAML provider with placeholder IdP values", async () => {
    const user = userEvent.setup();
    const createAuthProvider = vi
      .spyOn(authProvidersService, "createAuthProvider")
      .mockResolvedValue({ ...SAML_PROVIDER, id: 11 });
    renderAuthProvidersPage();
    const dialog = await openCreateModal(user);

    await selectProviderType(
      user,
      dialog,
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_SAML",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-create-submit"),
    );

    await waitFor(() =>
      expect(createAuthProvider).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({
          title: "Login with SAML",
          is_idp: true,
          details: expect.objectContaining({
            kind: "SamlProviderDetails",
            idp_entity_id: "https://PLACEHOLDER.COM/SAML/entity",
            sso_url: "https://PLACEHOLDER.COM/SAML/sso",
            sp_private_key: null,
          }),
        }),
      ),
    );
    expect(await screen.findByTestId("location")).toHaveTextContent("11");
  });

  it("stays on the list with the modal open when creating fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(authProvidersService, "createAuthProvider").mockRejectedValue(
      new Error("boom"),
    );
    renderAuthProvidersPage();
    const dialog = await openCreateModal(user);

    await selectProviderType(
      user,
      dialog,
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_GOOGLE",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-create-submit"),
    );

    await waitFor(() =>
      expect(
        within(dialog).getByTestId("super-admin-auth-provider-create-submit"),
      ).not.toBeDisabled(),
    );
    expect(screen.queryByTestId("location")).not.toBeInTheDocument();
    expect(
      screen.getByTestId("super-admin-auth-provider-create"),
    ).toBeInTheDocument();
  });

  it("offers the password type even when one already exists, leaving the backend's conflict to explain why it can't be created", async () => {
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      GITHUB_PROVIDER,
      PASSWORD_PROVIDER,
    ]);
    const createAuthProvider = vi
      .spyOn(authProvidersService, "createAuthProvider")
      .mockRejectedValue(
        new Error(
          "An integrated/password IDP provider already exists (id=2); only one is allowed",
        ),
      );
    const user = userEvent.setup();
    renderAuthProvidersPage();
    const dialog = await openCreateModal(user);

    await selectProviderType(
      user,
      dialog,
      "SUPER_ADMIN$AUTH_PROVIDER_KIND_PASSWORD",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-create-submit"),
    );

    await waitFor(() => expect(createAuthProvider).toHaveBeenCalledOnce());
    await waitFor(() =>
      expect(
        within(dialog).getByTestId("super-admin-auth-provider-create-submit"),
      ).not.toBeDisabled(),
    );
    expect(screen.queryByTestId("location")).not.toBeInTheDocument();
  });

  it("creates the integrated password provider", async () => {
    const user = userEvent.setup();
    const createAuthProvider = vi
      .spyOn(authProvidersService, "createAuthProvider")
      .mockResolvedValue(PASSWORD_PROVIDER);
    renderAuthProvidersPage();
    const dialog = await openCreateModal(user);

    await selectProviderType(
      user,
      dialog,
      "SUPER_ADMIN$AUTH_PROVIDER_KIND_PASSWORD",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-create-submit"),
    );

    await waitFor(() =>
      expect(createAuthProvider).toHaveBeenCalledExactlyOnceWith({
        title: "Login with Password",
        is_idp: true,
        icon: null,
        enabled: false,
        color: "#03A956",
        allow_registration: true,
        allow_match_by_email: true,
        details: { kind: "PasswordProviderDetails" },
      }),
    );
  });

  it("opens the provider's edit page from the row menu", async () => {
    const user = userEvent.setup();
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-1"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-1"));

    expect(await screen.findByTestId("location")).toHaveTextContent("1");
  });

  it("asks for confirmation naming the provider before deleting it", async () => {
    const user = userEvent.setup();
    const deleteAuthProvider = vi
      .spyOn(authProvidersService, "deleteAuthProvider")
      .mockResolvedValue(undefined);
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-1"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-delete-1"));

    const dialog = screen.getByTestId(
      "super-admin-auth-provider-delete-confirm",
    );
    expect(dialog).toHaveTextContent("GitHub");
    expect(deleteAuthProvider).not.toHaveBeenCalled();

    await user.click(
      within(dialog).getByTestId(
        "super-admin-auth-provider-delete-confirm-submit",
      ),
    );

    await waitFor(() =>
      expect(deleteAuthProvider).toHaveBeenCalledWith({ id: 1 }),
    );
  });

  it("keeps the provider when the delete is cancelled", async () => {
    const user = userEvent.setup();
    const deleteAuthProvider = vi
      .spyOn(authProvidersService, "deleteAuthProvider")
      .mockResolvedValue(undefined);
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-1"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-delete-1"));
    await user.click(
      screen.getByTestId("super-admin-auth-provider-delete-confirm-cancel"),
    );

    expect(
      screen.queryByTestId("super-admin-auth-provider-delete-confirm"),
    ).not.toBeInTheDocument();
    expect(deleteAuthProvider).not.toHaveBeenCalled();
    expect(screen.getByText("GitHub")).toBeInTheDocument();
  });

  it("navigates to the provider's User Links page from the row menu", async () => {
    const user = userEvent.setup();
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-1"),
    );
    await user.click(
      screen.getByTestId("super-admin-auth-provider-user-links-1"),
    );

    expect(await screen.findByTestId("location")).toHaveTextContent("1");
  });
});

// An Okta login provider that is on, and the same kind of provider that is on
// but not used for login.
const OKTA_LOGIN: AuthProvider = {
  ...GITHUB_PROVIDER,
  id: 6,
  title: "Okta",
  is_idp: true,
  details: {
    kind: "OAuthProviderDetails",
    provider_type: "enterprise_sso",
    client_id: "okta-client-id",
    client_secret: "**********",
    authorization_url: "https://acme.okta.example/oauth2/v1/authorize",
    token_url: "https://acme.okta.example/oauth2/v1/token",
    userinfo_url: "https://acme.okta.example/oauth2/v1/userinfo",
    scopes: ["openid", "email"],
    permitted_drift_seconds: 60,
    legacy_keycloak_type: null,
    named_secret: null,
    attribute_mapping: {},
  },
};
const OKTA_NOT_LOGIN: AuthProvider = {
  ...OKTA_LOGIN,
  id: 7,
  title: "Okta (not login)",
  is_idp: false,
};

describe("Super Admin Auth Providers page without SSO in the license", () => {
  beforeEach(() => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({ app_mode: "saas" }),
    );
    vi.spyOn(licenseService, "getStatus").mockResolvedValue({
      state: "missing_key",
      reason: null,
      offline: false,
      type: null,
      license_ends_at: null,
      expires_at: null,
      last_checked_at: null,
      customer_center_url: "https://customercenter.openhands.dev",
      features: { sso: false },
    });
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      GITHUB_PROVIDER,
      { ...SAML_PROVIDER, enabled: false },
      OKTA_LOGIN,
      OKTA_NOT_LOGIN,
    ]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const enabledSwitch = (title: string) =>
    screen.getByRole("switch", { name: `SUPER_ADMIN$COL_ENABLED: ${title}` });
  const loginSwitch = (title: string) =>
    screen.getByRole("switch", { name: `SUPER_ADMIN$COL_IS_IDP: ${title}` });

  // The license status loads after the providers. A locked switch shows it
  // has loaded.
  async function waitForLicense() {
    await screen.findByText("GitHub");
    await waitFor(() =>
      expect(enabledSwitch(SAML_PROVIDER.title)).toBeDisabled(),
    );
  }

  it("says SSO isn't in the license in the Add Provider modal, not on the page", async () => {
    const user = userEvent.setup();
    renderAuthProvidersPage();
    await waitForLicense();

    expect(
      screen.queryByTestId("sso-not-licensed-notice"),
    ).not.toBeInTheDocument();

    const dialog = await openCreateModal(user);
    const notice = within(dialog).getByTestId("sso-not-licensed-notice");
    expect(notice).toHaveTextContent("LICENSE$SSO_NOT_INCLUDED");
    expect(
      within(notice).getByRole("link", { name: "LICENSE$UPGRADE" }),
    ).toHaveAttribute("href", "https://customercenter.openhands.dev");
  });

  it("offers only git and password providers", async () => {
    const user = userEvent.setup();
    renderAuthProvidersPage();
    await waitForLicense();
    const dialog = await openCreateModal(user);

    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-type"),
    );
    const options = (await screen.findAllByRole("option")).map(
      (option) => option.textContent,
    );
    expect(options).toEqual([
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_GITHUB",
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_GITLAB",
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_BITBUCKET",
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_BITBUCKET_DATA_CENTER",
      "SUPER_ADMIN$AUTH_PROVIDER_PRESET_AZURE_DEVOPS",
      "SUPER_ADMIN$AUTH_PROVIDER_KIND_PASSWORD",
    ]);
  });

  it("locks only the switches that would turn on SSO login, and says why", async () => {
    renderAuthProvidersPage();
    await waitForLicense();

    expect(enabledSwitch(SAML_PROVIDER.title)).toHaveAttribute(
      "title",
      "LICENSE$SSO_NOT_INCLUDED",
    );
    expect(loginSwitch(OKTA_NOT_LOGIN.title)).toBeDisabled();
    expect(loginSwitch(OKTA_NOT_LOGIN.title)).toHaveAttribute(
      "title",
      "LICENSE$SSO_NOT_INCLUDED",
    );

    // SSO login that is on can still be turned off, and git providers are free.
    for (const toggle of [
      enabledSwitch(OKTA_LOGIN.title),
      loginSwitch(OKTA_LOGIN.title),
      enabledSwitch("GitHub"),
      loginSwitch("GitHub"),
    ]) {
      expect(toggle).toBeEnabled();
      expect(toggle).not.toHaveAttribute("title");
    }
  });
});
