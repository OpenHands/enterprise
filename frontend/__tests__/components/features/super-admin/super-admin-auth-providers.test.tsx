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
import type { AuthProvider } from "#/api/auth-providers-service/auth-providers.types";
import { DEFAULT_AUTH_PROVIDER_COLOR } from "#/api/auth-providers-service/auth-providers.types";
import { SuperAdminAuthProviders } from "#/components/features/super-admin/super-admin-auth-providers";
import translations from "#/i18n/translation.json";

const GITHUB_PROVIDER: AuthProvider = {
  id: 1,
  kind: "OAuthProviderDetails",
  title: "GitHub",
  is_idp: false,
  icon: null,
  color: DEFAULT_AUTH_PROVIDER_COLOR,
  allow_registration: true,
  allow_match_by_email: true,
  details: {
    kind: "OAuthProviderDetails",
    provider_type: "github",
    client_id: "github-client-id",
    client_secret: "**********",
    authorization_url: null,
    token_url: null,
    userinfo_url: null,
    scopes: ["repo"],
    permitted_drift_seconds: 60,
    legacy_keycloak_type: null,
  },
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

const KEYCLOAK_GITHUB_LOGIN_PROVIDER: AuthProvider = {
  id: 4,
  kind: "OAuthProviderDetails",
  title: "Log in with GitHub",
  is_idp: true,
  icon: null,
  color: DEFAULT_AUTH_PROVIDER_COLOR,
  allow_registration: true,
  allow_match_by_email: true,
  details: {
    kind: "OAuthProviderDetails",
    provider_type: "enterprise_sso",
    client_id: "keycloak-client-id",
    client_secret: "**********",
    authorization_url: "https://auth.example.com/auth?kc_idp_hint=github",
    token_url: "https://auth.example.com/token",
    userinfo_url: "https://auth.example.com/userinfo",
    scopes: ["openid", "email", "profile"],
    permitted_drift_seconds: 60,
    legacy_keycloak_type: "github",
  },
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

const PASSWORD_PROVIDER: AuthProvider = {
  id: 2,
  kind: "PasswordProviderDetails",
  title: "Password Login",
  is_idp: true,
  icon: null,
  color: DEFAULT_AUTH_PROVIDER_COLOR,
  allow_registration: true,
  allow_match_by_email: true,
  details: { kind: "PasswordProviderDetails" },
  created_at: "2024-01-02T00:00:00Z",
  updated_at: "2024-01-02T00:00:00Z",
};

const SAML_PROVIDER: AuthProvider = {
  id: 3,
  kind: "SamlProviderDetails",
  title: "Okta SAML",
  is_idp: true,
  icon: null,
  color: DEFAULT_AUTH_PROVIDER_COLOR,
  allow_registration: true,
  allow_match_by_email: true,
  details: {
    kind: "SamlProviderDetails",
    metadata_url: "https://idp.example.com/metadata",
    idp_entity_id: "https://idp.example.com/entity",
    sso_url: "https://idp.example.com/sso",
    sso_binding: "redirect",
    slo_url: "https://idp.example.com/slo",
    idp_x509_cert:
      "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----",
    sp_private_key: "**********",
    sp_x509_cert:
      "-----BEGIN CERTIFICATE-----\nMIIC...\n-----END CERTIFICATE-----",
    name_id_format: "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
    attribute_mapping: { email: "mail", name: "displayName" },
  },
  created_at: "2024-01-03T00:00:00Z",
  updated_at: "2024-01-03T00:00:00Z",
};

function renderAuthProvidersPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const RouterStub = createRoutesStub([
    {
      path: "/super-admin/auth-providers",
      Component: SuperAdminAuthProviders,
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

  it("defaults the type dropdown to OAuth and the provider dropdown to Enterprise SSO when adding a provider", async () => {
    const user = userEvent.setup();
    renderAuthProvidersPage();
    await screen.findByText("GitHub");

    await user.click(
      screen.getByRole("button", { name: "SUPER_ADMIN$ADD_AUTH_PROVIDER" }),
    );
    const dialog = screen.getByTestId("super-admin-auth-provider-form");

    expect(
      within(dialog).getByTestId("super-admin-auth-provider-kind"),
    ).toHaveValue("SUPER_ADMIN$AUTH_PROVIDER_KIND_OAUTH");
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-brand"),
    ).toHaveValue("Enterprise SSO");
    // No row id exists yet on a brand-new row, so there's nothing to build
    // a redirect URL out of.
    expect(
      within(dialog).queryByTestId("super-admin-auth-provider-redirect-url"),
    ).not.toBeInTheDocument();
  });

  it("creates a new OAuth provider from the form", async () => {
    const user = userEvent.setup();
    const createAuthProvider = vi
      .spyOn(authProvidersService, "createAuthProvider")
      .mockResolvedValue({ ...GITHUB_PROVIDER, id: 3, title: "GitLab" });
    renderAuthProvidersPage();
    await screen.findByText("GitHub");

    await user.click(
      screen.getByRole("button", { name: "SUPER_ADMIN$ADD_AUTH_PROVIDER" }),
    );
    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    const brandInput = within(dialog).getByTestId(
      "super-admin-auth-provider-brand",
    );
    await user.click(brandInput);
    await user.click(await screen.findByRole("option", { name: "GitLab" }));
    await user.type(
      within(dialog).getByTestId("super-admin-auth-provider-title"),
      "GitLab",
    );
    await user.type(
      within(dialog).getByTestId("super-admin-auth-provider-client-id"),
      "gitlab-client-id",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(createAuthProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "GitLab",
          details: expect.objectContaining({
            kind: "OAuthProviderDetails",
            provider_type: "gitlab",
            client_id: "gitlab-client-id",
          }),
        }),
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByTestId("super-admin-auth-provider-form"),
      ).not.toBeInTheDocument(),
    );
  });

  it("only shows the title field, and always sends is_idp=true, for the Integrated Password type", async () => {
    const user = userEvent.setup();
    const createAuthProvider = vi
      .spyOn(authProvidersService, "createAuthProvider")
      .mockResolvedValue(PASSWORD_PROVIDER);
    renderAuthProvidersPage();
    await screen.findByText("GitHub");

    await user.click(
      screen.getByRole("button", { name: "SUPER_ADMIN$ADD_AUTH_PROVIDER" }),
    );
    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    const kindInput = within(dialog).getByTestId(
      "super-admin-auth-provider-kind",
    );
    await user.click(kindInput);
    await user.click(
      await screen.findByRole("option", {
        name: "SUPER_ADMIN$AUTH_PROVIDER_KIND_PASSWORD",
      }),
    );

    expect(
      within(dialog).queryByTestId("super-admin-auth-provider-brand"),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByTestId("super-admin-auth-provider-client-id"),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByTestId("super-admin-auth-provider-client-secret"),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByTestId("super-admin-auth-provider-is-idp"),
    ).not.toBeInTheDocument();

    await user.type(
      within(dialog).getByTestId("super-admin-auth-provider-title"),
      "Password Login",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(createAuthProvider).toHaveBeenCalledWith({
        title: "Password Login",
        is_idp: true,
        icon: null,
        color: DEFAULT_AUTH_PROVIDER_COLOR,
        allow_registration: true,
        allow_match_by_email: true,
        details: { kind: "PasswordProviderDetails" },
      }),
    );
  });

  it("shows the OAuth redirect URL for an existing provider, copyable to the clipboard", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-1"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-1"));
    const dialog = screen.getByTestId("super-admin-auth-provider-form");

    const redirectUrlInput = within(dialog).getByTestId(
      "super-admin-auth-provider-redirect-url",
    );
    expect(redirectUrlInput).toHaveValue(
      `${window.location.origin}/oauth/1/callback`,
    );
    expect(redirectUrlInput).toHaveAttribute("readonly");

    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-redirect-url-copy"),
    );
    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/oauth/1/callback`,
    );
  });

  it("omits details (preserving the secret) when editing without touching any OAuth field", async () => {
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(GITHUB_PROVIDER);
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-1"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-1"));

    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-brand"),
    ).toHaveValue("GitHub");

    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledWith({
        id: 1,
        title: "GitHub",
        is_idp: false,
        icon: null,
        color: DEFAULT_AUTH_PROVIDER_COLOR,
        allow_registration: true,
        allow_match_by_email: true,
      }),
    );
  });

  it("sends details wholesale when an OAuth field is changed while editing", async () => {
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(GITHUB_PROVIDER);
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-1"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-1"));

    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    const clientIdInput = within(dialog).getByTestId(
      "super-admin-auth-provider-client-id",
    );
    await user.clear(clientIdInput);
    await user.type(clientIdInput, "github-client-id-2");
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 1,
          details: expect.objectContaining({
            kind: "OAuthProviderDetails",
            client_id: "github-client-id-2",
          }),
        }),
      ),
    );
    const { details } = updateAuthProvider.mock.calls[0][0];
    expect(details).not.toHaveProperty("client_secret");
  });

  it("sends the new client secret when one is typed while editing", async () => {
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(GITHUB_PROVIDER);
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-1"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-1"));

    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    await user.type(
      within(dialog).getByTestId("super-admin-auth-provider-client-secret"),
      "new-secret",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 1,
          details: expect.objectContaining({ client_secret: "new-secret" }),
        }),
      ),
    );
  });

  it("loads legacy_keycloak_type into the edit form and keeps it when another OAuth field is changed", async () => {
    const user = userEvent.setup();
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      KEYCLOAK_GITHUB_LOGIN_PROVIDER,
    ]);
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(KEYCLOAK_GITHUB_LOGIN_PROVIDER);
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-4"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-4"));

    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    expect(
      within(dialog).getByTestId(
        "super-admin-auth-provider-legacy-keycloak-type",
      ),
    ).toHaveValue("GitHub");

    const scopesInput = within(dialog).getByTestId(
      "super-admin-auth-provider-scopes",
    );
    await user.clear(scopesInput);
    await user.type(scopesInput, "openid");
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 4,
          details: expect.objectContaining({
            provider_type: "enterprise_sso",
            scopes: ["openid"],
            legacy_keycloak_type: "github",
          }),
        }),
      ),
    );
  });

  it("clears legacy_keycloak_type when the field is cleared", async () => {
    const user = userEvent.setup();
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      KEYCLOAK_GITHUB_LOGIN_PROVIDER,
    ]);
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(KEYCLOAK_GITHUB_LOGIN_PROVIDER);
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-4"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-4"));

    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    await user.clear(
      within(dialog).getByTestId(
        "super-admin-auth-provider-legacy-keycloak-type",
      ),
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          details: expect.objectContaining({ legacy_keycloak_type: null }),
        }),
      ),
    );
  });

  it("lists a SAML provider by its kind label", async () => {
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      GITHUB_PROVIDER,
      SAML_PROVIDER,
    ]);
    renderAuthProvidersPage();

    expect(await screen.findByText("Okta SAML")).toBeInTheDocument();
    expect(
      screen.getByText("SUPER_ADMIN$AUTH_PROVIDER_KIND_SAML"),
    ).toBeInTheDocument();

    const samlIsIdpSwitch = screen.getByRole("switch", {
      name: "SUPER_ADMIN$COL_IS_IDP: Okta SAML",
    });
    expect(samlIsIdpSwitch).toBeDisabled();
    expect(samlIsIdpSwitch).toHaveAttribute("aria-checked", "true");
  });

  it("shows only the SAML fields, hides the IDP switch, and always sends is_idp=true when adding a SAML provider", async () => {
    const user = userEvent.setup();
    const createAuthProvider = vi
      .spyOn(authProvidersService, "createAuthProvider")
      .mockResolvedValue(SAML_PROVIDER);
    renderAuthProvidersPage();
    await screen.findByText("GitHub");

    await user.click(
      screen.getByRole("button", { name: "SUPER_ADMIN$ADD_AUTH_PROVIDER" }),
    );
    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    const kindInput = within(dialog).getByTestId(
      "super-admin-auth-provider-kind",
    );
    await user.click(kindInput);
    await user.click(
      await screen.findByRole("option", {
        name: "SUPER_ADMIN$AUTH_PROVIDER_KIND_SAML",
      }),
    );

    expect(
      within(dialog).queryByTestId("super-admin-auth-provider-brand"),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByTestId("super-admin-auth-provider-client-id"),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByTestId("super-admin-auth-provider-is-idp"),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-metadata-url"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-idp-entity-id"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-sso-url"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-sso-binding"),
    ).toHaveValue("SUPER_ADMIN$AUTH_PROVIDER_SSO_BINDING_REDIRECT");
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-slo-url"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-idp-x509-cert"),
    ).toBeInTheDocument();
    // No row id exists yet on a brand-new row, so there's nothing to build
    // an SP metadata URL out of (see `_sp_entity_id`).
    expect(
      within(dialog).queryByTestId("super-admin-auth-provider-sp-metadata-url"),
    ).not.toBeInTheDocument();
    // No key exists yet on a brand-new row -- the SP certificate (and the
    // "SP Private Key" field that used to live here) are never
    // admin-typed, see `_ensure_sp_keypair`.
    expect(
      within(dialog).queryByTestId("super-admin-auth-provider-sp-x509-cert"),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByTestId(
        "super-admin-auth-provider-sp-x509-cert-download",
      ),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByTestId(
        "super-admin-auth-provider-sp-x509-cert-pending",
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-name-id-format"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-attribute-mapping"),
    ).toBeInTheDocument();

    await user.type(
      within(dialog).getByTestId("super-admin-auth-provider-title"),
      "Okta SAML",
    );
    await user.type(
      within(dialog).getByTestId("super-admin-auth-provider-idp-entity-id"),
      "https://idp.example.com/entity",
    );
    await user.type(
      within(dialog).getByTestId("super-admin-auth-provider-sso-url"),
      "https://idp.example.com/sso",
    );
    await user.type(
      within(dialog).getByTestId("super-admin-auth-provider-attribute-mapping"),
      "email=urn:oid:0.9.2342.19200300.100.1.3",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(createAuthProvider).toHaveBeenCalledWith({
        title: "Okta SAML",
        is_idp: true,
        icon: null,
        color: DEFAULT_AUTH_PROVIDER_COLOR,
        allow_registration: true,
        allow_match_by_email: true,
        details: {
          kind: "SamlProviderDetails",
          metadata_url: null,
          idp_entity_id: "https://idp.example.com/entity",
          sso_url: "https://idp.example.com/sso",
          sso_binding: "redirect",
          slo_url: null,
          idp_x509_cert: null,
          sp_private_key: null,
          sp_x509_cert: null,
          name_id_format: null,
          attribute_mapping: {
            email: "urn:oid:0.9.2342.19200300.100.1.3",
          },
        },
      }),
    );
  });

  it("lets a SAML provider be created with just a title, with no IdP details yet", async () => {
    // Plenty of IdPs need this provider's generated SP certificate and URLs
    // *before* they'll hand over their own entity ID/SSO URL/cert -- the
    // row has to be creatable (to mint that key) first. See
    // SUPER_ADMIN$AUTH_PROVIDER_SAML_FIELDS_OPTIONAL_HINT.
    const user = userEvent.setup();
    const createAuthProvider = vi
      .spyOn(authProvidersService, "createAuthProvider")
      .mockResolvedValue(SAML_PROVIDER);
    renderAuthProvidersPage();
    await screen.findByText("GitHub");

    await user.click(
      screen.getByRole("button", { name: "SUPER_ADMIN$ADD_AUTH_PROVIDER" }),
    );
    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-kind"),
    );
    await user.click(
      await screen.findByRole("option", {
        name: "SUPER_ADMIN$AUTH_PROVIDER_KIND_SAML",
      }),
    );
    expect(
      within(dialog).getByTestId(
        "super-admin-auth-provider-saml-fields-optional-hint",
      ),
    ).toBeInTheDocument();

    const submit = within(dialog).getByTestId(
      "super-admin-auth-provider-form-submit",
    );
    expect(submit).toBeDisabled();

    await user.type(
      within(dialog).getByTestId("super-admin-auth-provider-title"),
      "Okta SAML",
    );
    expect(submit).not.toBeDisabled();

    await user.click(submit);

    await waitFor(() =>
      expect(createAuthProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Okta SAML",
          details: expect.objectContaining({
            kind: "SamlProviderDetails",
            metadata_url: null,
            idp_entity_id: null,
            sso_url: null,
          }),
        }),
      ),
    );
  });

  it("auto-fills the IdP fields from the metadata URL once it's entered", async () => {
    const previewSamlMetadata = vi
      .spyOn(authProvidersService, "previewSamlMetadata")
      .mockResolvedValue({
        idp_entity_id: "https://idp.example.com/entity",
        sso_url: "https://idp.example.com/sso",
        sso_binding: "post",
        slo_url: "https://idp.example.com/slo",
        idp_x509_cert:
          "-----BEGIN CERTIFICATE-----MIIB...-----END CERTIFICATE-----",
        name_id_format: "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
      });
    const user = userEvent.setup();
    renderAuthProvidersPage();
    await screen.findByText("GitHub");

    await user.click(
      screen.getByRole("button", { name: "SUPER_ADMIN$ADD_AUTH_PROVIDER" }),
    );
    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-kind"),
    );
    await user.click(
      await screen.findByRole("option", {
        name: "SUPER_ADMIN$AUTH_PROVIDER_KIND_SAML",
      }),
    );

    await user.type(
      within(dialog).getByTestId("super-admin-auth-provider-metadata-url"),
      "https://idp.example.com/metadata",
    );
    await user.tab();

    await waitFor(() =>
      expect(previewSamlMetadata).toHaveBeenCalledWith(
        "https://idp.example.com/metadata",
      ),
    );
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-idp-entity-id"),
    ).toHaveValue("https://idp.example.com/entity");
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-sso-url"),
    ).toHaveValue("https://idp.example.com/sso");
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-sso-binding"),
    ).toHaveValue("SUPER_ADMIN$AUTH_PROVIDER_SSO_BINDING_POST");
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-slo-url"),
    ).toHaveValue("https://idp.example.com/slo");
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-idp-x509-cert"),
    ).toHaveValue(
      "-----BEGIN CERTIFICATE-----MIIB...-----END CERTIFICATE-----",
    );
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-name-id-format"),
    ).toHaveValue("urn:oasis:names:tc:SAML:2.0:nameid-format:persistent");

    // Blurring again without changing the URL shouldn't re-fetch.
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-metadata-url"),
    );
    await user.tab();
    expect(previewSamlMetadata).toHaveBeenCalledTimes(1);
  });

  it("shows an inline error when the metadata URL can't be fetched, without blocking manual entry", async () => {
    const previewSamlMetadata = vi
      .spyOn(authProvidersService, "previewSamlMetadata")
      .mockRejectedValue(new Error("Failed to fetch metadata URL: 404"));
    const user = userEvent.setup();
    renderAuthProvidersPage();
    await screen.findByText("GitHub");

    await user.click(
      screen.getByRole("button", { name: "SUPER_ADMIN$ADD_AUTH_PROVIDER" }),
    );
    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-kind"),
    );
    await user.click(
      await screen.findByRole("option", {
        name: "SUPER_ADMIN$AUTH_PROVIDER_KIND_SAML",
      }),
    );

    await user.type(
      within(dialog).getByTestId("super-admin-auth-provider-metadata-url"),
      "https://idp.example.com/metadata",
    );
    await user.tab();

    await waitFor(() => expect(previewSamlMetadata).toHaveBeenCalled());
    // The real backend error message is shown when there is one (just like
    // every other mutation in this form) -- the generic
    // METADATA_FETCH_ERROR copy is only a fallback.
    expect(
      within(dialog).getByTestId(
        "super-admin-auth-provider-metadata-url-error",
      ),
    ).toHaveTextContent("Failed to fetch metadata URL: 404");

    // Still fillable/submittable by hand -- the title is the only hard
    // requirement for a SAML row.
    await user.type(
      within(dialog).getByTestId("super-admin-auth-provider-title"),
      "Okta SAML",
    );
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-form-submit"),
    ).not.toBeDisabled();
  });

  it("loads an existing SAML provider's fields into the edit form", async () => {
    const user = userEvent.setup();
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      SAML_PROVIDER,
    ]);
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-3"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-3"));

    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-metadata-url"),
    ).toHaveValue("https://idp.example.com/metadata");
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-idp-entity-id"),
    ).toHaveValue("https://idp.example.com/entity");
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-sso-url"),
    ).toHaveValue("https://idp.example.com/sso");
    // A `<textarea>`, unlike a plain `<input type="text">`, preserves the
    // certificate's embedded newlines instead of collapsing them.
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-idp-x509-cert"),
    ).toHaveValue(
      "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----",
    );
    // Read-only: shows the cert already generated for this row, never an
    // input the admin can type into. Also a `<textarea>`, so its embedded
    // newlines are preserved the same way as the IdP cert field above.
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-sp-x509-cert"),
    ).toHaveValue(
      "-----BEGIN CERTIFICATE-----\nMIIC...\n-----END CERTIFICATE-----",
    );
  });

  it("shows the SP metadata URL for an existing SAML provider, copyable to the clipboard", async () => {
    const user = userEvent.setup();
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      SAML_PROVIDER,
    ]);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-3"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-3"));
    const dialog = screen.getByTestId("super-admin-auth-provider-form");

    const spMetadataUrlInput = within(dialog).getByTestId(
      "super-admin-auth-provider-sp-metadata-url",
    );
    expect(spMetadataUrlInput).toHaveValue(
      `${window.location.origin}/saml/3/metadata`,
    );
    expect(spMetadataUrlInput).toHaveAttribute("readonly");

    await user.click(
      within(dialog).getByTestId(
        "super-admin-auth-provider-sp-metadata-url-copy",
      ),
    );
    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/saml/3/metadata`,
    );
  });

  it("downloads the SP certificate as a PEM file", async () => {
    const user = userEvent.setup();
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      SAML_PROVIDER,
    ]);
    const createObjectURL = vi.fn().mockReturnValue("blob:mock-sp-certificate");
    const revokeObjectURL = vi.fn();
    Object.assign(window.URL, { createObjectURL, revokeObjectURL });
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-3"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-3"));
    const dialog = screen.getByTestId("super-admin-auth-provider-form");

    await user.click(
      within(dialog).getByTestId(
        "super-admin-auth-provider-sp-x509-cert-download",
      ),
    );

    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-sp-certificate");
  });

  it("omits details (preserving the SP private key) when editing a SAML provider without touching any SAML field", async () => {
    const user = userEvent.setup();
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      SAML_PROVIDER,
    ]);
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(SAML_PROVIDER);
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-3"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-3"));
    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledWith({
        id: 3,
        title: "Okta SAML",
        is_idp: true,
        icon: null,
        color: DEFAULT_AUTH_PROVIDER_COLOR,
        allow_registration: true,
        allow_match_by_email: true,
      }),
    );
  });

  it("loads an existing attribute mapping into its form field, and preserves it when a different SAML field is edited", async () => {
    const user = userEvent.setup();
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      SAML_PROVIDER,
    ]);
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(SAML_PROVIDER);
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-3"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-3"));
    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-attribute-mapping"),
    ).toHaveValue("email=mail, name=displayName");

    const sloUrlInput = within(dialog).getByTestId(
      "super-admin-auth-provider-slo-url",
    );
    await user.clear(sloUrlInput);
    await user.type(sloUrlInput, "https://idp.example.com/slo-2");
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 3,
          details: expect.objectContaining({
            attribute_mapping: { email: "mail", name: "displayName" },
          }),
        }),
      ),
    );
  });

  it("sends details wholesale when a SAML field is changed while editing", async () => {
    const user = userEvent.setup();
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      SAML_PROVIDER,
    ]);
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(SAML_PROVIDER);
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-3"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-3"));
    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    const ssoUrlInput = within(dialog).getByTestId(
      "super-admin-auth-provider-sso-url",
    );
    await user.clear(ssoUrlInput);
    await user.type(ssoUrlInput, "https://idp.example.com/sso-2");
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 3,
          details: expect.objectContaining({
            kind: "SamlProviderDetails",
            sso_url: "https://idp.example.com/sso-2",
            sp_private_key: null,
            sp_x509_cert: null,
          }),
        }),
      ),
    );
  });

  it("renders the metadata URL help text on its own line below the label, not crowded next to it", async () => {
    const user = userEvent.setup();
    renderAuthProvidersPage();
    await screen.findByText("GitHub");

    await user.click(
      screen.getByRole("button", { name: "SUPER_ADMIN$ADD_AUTH_PROVIDER" }),
    );
    const dialog = screen.getByTestId("super-admin-auth-provider-form");
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-kind"),
    );
    await user.click(
      await screen.findByRole("option", {
        name: "SUPER_ADMIN$AUTH_PROVIDER_KIND_SAML",
      }),
    );

    const hint = within(dialog).getByTestId(
      "super-admin-auth-provider-metadata-url-hint",
    );
    expect(hint).toBeInTheDocument();
    // `description` renders as its own block between the label row and the
    // `<input>`, so the hint and the label are siblings in different rows
    // (the label's row has no knowledge of the hint at all) rather than
    // the hint being squeezed into the label's own row as `hint` would do.
    const labelRow = within(dialog)
      .getByText("SUPER_ADMIN$AUTH_PROVIDER_METADATA_URL")
      .closest("div");
    expect(labelRow).not.toContainElement(hint);
  });

  it("shows a read-only SP certificate once a SAML provider has been saved, with no way to type a private key", async () => {
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      SAML_PROVIDER,
    ]);
    const user = userEvent.setup();
    renderAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-3"),
    );
    await user.click(screen.getByTestId("super-admin-auth-provider-edit-3"));
    const dialog = screen.getByTestId("super-admin-auth-provider-form");

    expect(
      within(dialog).getByTestId("super-admin-auth-provider-sp-x509-cert"),
    ).toHaveAttribute("readonly");
    expect(
      within(dialog).queryByTestId("super-admin-auth-provider-sp-private-key"),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByTestId(
        "super-admin-auth-provider-sp-x509-cert-pending",
      ),
    ).not.toBeInTheDocument();
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
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const RouterStub = createRoutesStub([
      {
        path: "/super-admin/auth-providers",
        Component: SuperAdminAuthProviders,
      },
      {
        path: "/super-admin/auth-providers/:providerId/users",
        Component: () => {
          const { providerId } = useParams<{ providerId: string }>();
          return <p data-testid="location">{providerId}</p>;
        },
      },
    ]);
    render(
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={queryClient}>
          <RouterStub initialEntries={["/super-admin/auth-providers"]} />
        </QueryClientProvider>
      </I18nextProvider>,
    );

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-actions-1"),
    );
    await user.click(
      screen.getByTestId("super-admin-auth-provider-user-links-1"),
    );

    expect(await screen.findByTestId("location")).toHaveTextContent("1");
  });
});
