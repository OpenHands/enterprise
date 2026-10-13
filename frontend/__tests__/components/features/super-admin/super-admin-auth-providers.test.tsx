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
import { authProvidersService } from "#/api/auth-providers-service/auth-providers-service.api";
import type { AuthProvider } from "#/api/auth-providers-service/auth-providers.types";
import { SuperAdminAuthProviders } from "#/components/features/super-admin/super-admin-auth-providers";
import translations from "#/i18n/translation.json";

const GITHUB_PROVIDER: AuthProvider = {
  id: 1,
  kind: "OAuthProviderDetails",
  title: "GitHub",
  is_idp: false,
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
  },
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

const PASSWORD_PROVIDER: AuthProvider = {
  id: 2,
  kind: "PasswordProviderDetails",
  title: "Password Login",
  is_idp: true,
  details: { kind: "PasswordProviderDetails" },
  created_at: "2024-01-02T00:00:00Z",
  updated_at: "2024-01-02T00:00:00Z",
};

const SAML_PROVIDER: AuthProvider = {
  id: 3,
  kind: "SamlProviderDetails",
  title: "Okta SAML",
  is_idp: true,
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

  it("lists configured auth providers by title, kind, and a read-only IDP switch, without client id/secret columns", async () => {
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      GITHUB_PROVIDER,
      PASSWORD_PROVIDER,
    ]);
    renderAuthProvidersPage();

    expect(await screen.findByText("GitHub")).toBeInTheDocument();
    expect(
      screen.getByText("SUPER_ADMIN$AUTH_PROVIDER_KIND_OAUTH"),
    ).toBeInTheDocument();

    const githubIsIdpSwitch = screen.getByRole("switch", {
      name: "SUPER_ADMIN$COL_IS_IDP: GitHub",
    });
    expect(githubIsIdpSwitch).toBeDisabled();
    expect(githubIsIdpSwitch).toHaveAttribute("aria-checked", "false");

    const passwordIsIdpSwitch = screen.getByRole("switch", {
      name: "SUPER_ADMIN$COL_IS_IDP: Password Login",
    });
    expect(passwordIsIdpSwitch).toBeDisabled();
    expect(passwordIsIdpSwitch).toHaveAttribute("aria-checked", "true");

    expect(screen.queryByText("github-client-id")).not.toBeInTheDocument();
    expect(
      screen.queryByText("SUPER_ADMIN$OAUTH_PROVIDER_SECRET_SET"),
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
        details: { kind: "PasswordProviderDetails" },
      }),
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
            client_secret: null,
          }),
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
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-sp-private-key"),
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
      "email=mail, name=displayName",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(createAuthProvider).toHaveBeenCalledWith({
        title: "Okta SAML",
        is_idp: true,
        details: {
          kind: "SamlProviderDetails",
          metadata_url: null,
          idp_entity_id: "https://idp.example.com/entity",
          sso_url: "https://idp.example.com/sso",
          sso_binding: "redirect",
          slo_url: null,
          idp_x509_cert: null,
          sp_private_key: null,
          name_id_format: null,
          attribute_mapping: { email: "mail", name: "displayName" },
        },
      }),
    );
  });

  it("disables submit for a SAML provider until a metadata URL or an entity ID + SSO URL pair is entered", async () => {
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
      within(dialog).getByTestId("super-admin-auth-provider-title"),
      "Okta SAML",
    );

    const submit = within(dialog).getByTestId(
      "super-admin-auth-provider-form-submit",
    );
    expect(submit).toBeDisabled();

    await user.type(
      within(dialog).getByTestId("super-admin-auth-provider-metadata-url"),
      "https://idp.example.com/metadata",
    );
    expect(submit).not.toBeDisabled();
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
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-attribute-mapping"),
    ).toHaveValue("email=mail, name=displayName");
    expect(
      within(dialog).getByTestId("super-admin-auth-provider-sp-private-key"),
    ).toHaveValue("");
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
      }),
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
          }),
        }),
      ),
    );
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
});
