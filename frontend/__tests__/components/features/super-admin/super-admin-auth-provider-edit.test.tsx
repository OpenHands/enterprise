import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { authProvidersService } from "#/api/auth-providers-service/auth-providers-service.api";
import type { AuthProvider } from "#/api/auth-providers-service/auth-providers.types";
import { DEFAULT_AUTH_PROVIDER_COLOR } from "#/api/auth-providers-service/auth-providers.types";
import { SuperAdminAuthProviderEdit } from "#/components/features/super-admin/super-admin-auth-provider-edit";
import {
  GITHUB_PROVIDER,
  KEYCLOAK_GITHUB_LOGIN_PROVIDER,
  SAML_PROVIDER,
} from "./auth-provider-fixtures";

/** A freshly created preset row: placeholder values and no secret yet. */
const NEW_OAUTH_PROVIDER: AuthProvider = {
  ...GITHUB_PROVIDER,
  id: 5,
  title: "Log in with Google",
  is_idp: true,
  details: {
    kind: "OAuthProviderDetails",
    provider_type: "enterprise_sso",
    client_id: "PLACEHOLDER",
    client_secret: null,
    authorization_url: "https://accounts.google.com/o/oauth2/auth",
    token_url: "https://oauth2.googleapis.com/token",
    userinfo_url: "https://openidconnect.googleapis.com/v1/userinfo",
    scopes: ["openid", "email"],
    permitted_drift_seconds: 60,
    legacy_keycloak_type: null,
  },
};

function renderEditPage(provider: AuthProvider) {
  vi.spyOn(authProvidersService, "getAuthProvider").mockResolvedValue(provider);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const RouterStub = createRoutesStub([
    {
      path: "/super-admin/auth-providers/:providerId",
      Component: SuperAdminAuthProviderEdit,
    },
    {
      path: "/super-admin/auth-providers",
      Component: () => <p data-testid="auth-providers-list" />,
    },
  ]);
  render(
    <QueryClientProvider client={queryClient}>
      <RouterStub
        initialEntries={[`/super-admin/auth-providers/${provider.id}`]}
      />
    </QueryClientProvider>,
  );
  return screen.findByTestId("super-admin-auth-provider-form");
}

async function selectKind(
  user: ReturnType<typeof userEvent.setup>,
  form: HTMLElement,
  optionName: string,
) {
  await user.click(within(form).getByTestId("super-admin-auth-provider-kind"));
  await user.click(await screen.findByRole("option", { name: optionName }));
}

describe("Super Admin Auth Provider edit page", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the provider from its URL and shows its title in the header", async () => {
    const form = await renderEditPage(GITHUB_PROVIDER);

    expect(authProvidersService.getAuthProvider).toHaveBeenCalledWith(1);
    expect(screen.getByText("SUPER_ADMIN$EDIT_AUTH_PROVIDER")).toBeVisible();
    expect(
      within(form).getByTestId("super-admin-auth-provider-title"),
    ).toHaveValue("GitHub");
    expect(
      within(form).getByTestId("super-admin-auth-provider-brand"),
    ).toHaveValue("GitHub");
  });

  it("shows a load error when the provider can't be fetched", async () => {
    vi.spyOn(authProvidersService, "getAuthProvider").mockRejectedValue(
      new Error("not found"),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const RouterStub = createRoutesStub([
      {
        path: "/super-admin/auth-providers/:providerId",
        Component: SuperAdminAuthProviderEdit,
      },
    ]);
    render(
      <QueryClientProvider client={queryClient}>
        <RouterStub initialEntries={["/super-admin/auth-providers/99"]} />
      </QueryClientProvider>,
    );

    expect(
      await screen.findByText("SUPER_ADMIN$LOAD_ERROR"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("super-admin-auth-provider-form"),
    ).not.toBeInTheDocument();
  });

  it("links back to the auth providers list", async () => {
    const user = userEvent.setup();
    await renderEditPage(GITHUB_PROVIDER);

    const back = screen.getByTestId("super-admin-auth-provider-edit-back");
    expect(back).toHaveTextContent("SUPER_ADMIN$BACK_TO_AUTH_PROVIDERS");
    await user.click(back);

    expect(
      await screen.findByTestId("auth-providers-list"),
    ).toBeInTheDocument();
  });

  it("returns to the list when cancelled", async () => {
    const user = userEvent.setup();
    const form = await renderEditPage(GITHUB_PROVIDER);

    await user.click(
      within(form).getByTestId("super-admin-auth-provider-form-cancel"),
    );

    expect(
      await screen.findByTestId("auth-providers-list"),
    ).toBeInTheDocument();
  });

  it("only enables saving once something has changed", async () => {
    const user = userEvent.setup();
    const form = await renderEditPage(GITHUB_PROVIDER);
    const submit = within(form).getByTestId(
      "super-admin-auth-provider-form-submit",
    );
    expect(submit).toBeDisabled();

    await user.type(
      within(form).getByTestId("super-admin-auth-provider-title"),
      "!",
    );

    expect(submit).not.toBeDisabled();
  });

  it("shows the OAuth redirect URL, copyable to the clipboard, for a freshly created provider", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    const form = await renderEditPage(NEW_OAUTH_PROVIDER);

    const redirectUrlInput = within(form).getByTestId(
      "super-admin-auth-provider-redirect-url",
    );
    expect(redirectUrlInput).toHaveValue(
      `${window.location.origin}/oauth/5/callback`,
    );
    expect(redirectUrlInput).toHaveAttribute("readonly");

    await user.click(
      within(form).getByTestId("super-admin-auth-provider-redirect-url-copy"),
    );
    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/oauth/5/callback`,
    );
  });

  it("only says the current secret will be kept when one is configured", async () => {
    const form = await renderEditPage(NEW_OAUTH_PROVIDER);
    expect(
      within(form).queryByText("SUPER_ADMIN$AUTH_PROVIDER_CLIENT_SECRET_HINT"),
    ).not.toBeInTheDocument();
    expect(
      within(form).getByTestId("super-admin-auth-provider-client-secret"),
    ).not.toHaveAttribute("placeholder");
  });

  it("tells the admin a blank secret keeps the configured one", async () => {
    const form = await renderEditPage(GITHUB_PROVIDER);
    expect(
      within(form).getByText("SUPER_ADMIN$AUTH_PROVIDER_CLIENT_SECRET_HINT"),
    ).toBeInTheDocument();
  });

  it("fills in a freshly created provider's real client id and secret", async () => {
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(NEW_OAUTH_PROVIDER);
    const form = await renderEditPage(NEW_OAUTH_PROVIDER);

    const clientIdInput = within(form).getByTestId(
      "super-admin-auth-provider-client-id",
    );
    await user.clear(clientIdInput);
    await user.type(clientIdInput, "real-client-id");
    await user.type(
      within(form).getByTestId("super-admin-auth-provider-client-secret"),
      "real-secret",
    );
    await user.click(
      within(form).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledExactlyOnceWith({
        id: 5,
        title: "Log in with Google",
        is_idp: true,
        icon: null,
        color: DEFAULT_AUTH_PROVIDER_COLOR,
        allow_registration: true,
        allow_match_by_email: true,
        details: {
          kind: "OAuthProviderDetails",
          provider_type: "enterprise_sso",
          client_id: "real-client-id",
          client_secret: "real-secret",
          authorization_url: "https://accounts.google.com/o/oauth2/auth",
          token_url: "https://oauth2.googleapis.com/token",
          userinfo_url: "https://openidconnect.googleapis.com/v1/userinfo",
          scopes: ["openid", "email"],
          permitted_drift_seconds: 60,
          legacy_keycloak_type: null,
        },
      }),
    );
  });

  it("omits details (preserving the secret) when only a non-OAuth field changes", async () => {
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(GITHUB_PROVIDER);
    const form = await renderEditPage(GITHUB_PROVIDER);

    const titleInput = within(form).getByTestId(
      "super-admin-auth-provider-title",
    );
    await user.clear(titleInput);
    await user.type(titleInput, "GitHub Enterprise");
    await user.click(
      within(form).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledWith({
        id: 1,
        title: "GitHub Enterprise",
        is_idp: false,
        icon: null,
        color: DEFAULT_AUTH_PROVIDER_COLOR,
        allow_registration: true,
        allow_match_by_email: true,
      }),
    );
  });

  it("sends details wholesale when an OAuth field is changed", async () => {
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(GITHUB_PROVIDER);
    const form = await renderEditPage(GITHUB_PROVIDER);

    const clientIdInput = within(form).getByTestId(
      "super-admin-auth-provider-client-id",
    );
    await user.clear(clientIdInput);
    await user.type(clientIdInput, "github-client-id-2");
    await user.click(
      within(form).getByTestId("super-admin-auth-provider-form-submit"),
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

  it("requires a client id for an OAuth provider", async () => {
    const user = userEvent.setup();
    const form = await renderEditPage(GITHUB_PROVIDER);

    await user.clear(
      within(form).getByTestId("super-admin-auth-provider-client-id"),
    );

    expect(
      within(form).getByTestId("super-admin-auth-provider-form-submit"),
    ).toBeDisabled();
  });

  it("loads legacy_keycloak_type and keeps it when another OAuth field is changed", async () => {
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(KEYCLOAK_GITHUB_LOGIN_PROVIDER);
    const form = await renderEditPage(KEYCLOAK_GITHUB_LOGIN_PROVIDER);

    expect(
      within(form).getByTestId(
        "super-admin-auth-provider-legacy-keycloak-type",
      ),
    ).toHaveValue("GitHub");

    const scopesInput = within(form).getByTestId(
      "super-admin-auth-provider-scopes",
    );
    await user.clear(scopesInput);
    await user.type(scopesInput, "openid");
    await user.click(
      within(form).getByTestId("super-admin-auth-provider-form-submit"),
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
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(KEYCLOAK_GITHUB_LOGIN_PROVIDER);
    const form = await renderEditPage(KEYCLOAK_GITHUB_LOGIN_PROVIDER);

    await user.clear(
      within(form).getByTestId(
        "super-admin-auth-provider-legacy-keycloak-type",
      ),
    );
    await user.click(
      within(form).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          details: expect.objectContaining({ legacy_keycloak_type: null }),
        }),
      ),
    );
  });

  it("hides the OAuth fields and always sends is_idp=true when switched to Integrated Password", async () => {
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(GITHUB_PROVIDER);
    const form = await renderEditPage(GITHUB_PROVIDER);

    await selectKind(user, form, "SUPER_ADMIN$AUTH_PROVIDER_KIND_PASSWORD");

    expect(
      within(form).queryByTestId("super-admin-auth-provider-brand"),
    ).not.toBeInTheDocument();
    expect(
      within(form).queryByTestId("super-admin-auth-provider-client-id"),
    ).not.toBeInTheDocument();
    expect(
      within(form).queryByTestId("super-admin-auth-provider-is-idp"),
    ).not.toBeInTheDocument();

    await user.click(
      within(form).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 1,
          is_idp: true,
          details: { kind: "PasswordProviderDetails" },
        }),
      ),
    );
  });

  it("shows only the SAML fields when switched to SAML, without URLs or a certificate until saved", async () => {
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(SAML_PROVIDER);
    const form = await renderEditPage(GITHUB_PROVIDER);

    await selectKind(user, form, "SUPER_ADMIN$AUTH_PROVIDER_KIND_SAML");

    expect(
      within(form).queryByTestId("super-admin-auth-provider-client-id"),
    ).not.toBeInTheDocument();
    expect(
      within(form).queryByTestId("super-admin-auth-provider-is-idp"),
    ).not.toBeInTheDocument();
    expect(
      within(form).getByTestId(
        "super-admin-auth-provider-saml-fields-optional-hint",
      ),
    ).toBeInTheDocument();
    expect(
      within(form).getByTestId("super-admin-auth-provider-sso-binding"),
    ).toHaveValue("SUPER_ADMIN$AUTH_PROVIDER_SSO_BINDING_REDIRECT");
    // The persisted row is still OAuth: `/saml/{id}/metadata` 404s until
    // this switch is saved.
    expect(
      within(form).queryByTestId("super-admin-auth-provider-sp-metadata-url"),
    ).not.toBeInTheDocument();
    expect(
      within(form).queryByTestId("super-admin-auth-provider-redirect-url"),
    ).not.toBeInTheDocument();
    expect(
      within(form).getByTestId(
        "super-admin-auth-provider-sp-x509-cert-pending",
      ),
    ).toBeInTheDocument();

    await user.type(
      within(form).getByTestId("super-admin-auth-provider-sso-url"),
      "https://idp.example.com/sso",
    );
    await user.type(
      within(form).getByTestId("super-admin-auth-provider-attribute-mapping"),
      "email=urn:oid:0.9.2342.19200300.100.1.3",
    );
    await user.click(
      within(form).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledWith({
        id: 1,
        title: "GitHub",
        is_idp: true,
        icon: null,
        color: DEFAULT_AUTH_PROVIDER_COLOR,
        allow_registration: true,
        allow_match_by_email: true,
        details: {
          kind: "SamlProviderDetails",
          metadata_url: null,
          idp_entity_id: null,
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

  it("loads an existing SAML provider's fields", async () => {
    const form = await renderEditPage(SAML_PROVIDER);

    expect(
      within(form).getByTestId("super-admin-auth-provider-metadata-url"),
    ).toHaveValue("https://idp.example.com/metadata");
    expect(
      within(form).getByTestId("super-admin-auth-provider-idp-entity-id"),
    ).toHaveValue("https://idp.example.com/entity");
    expect(
      within(form).getByTestId("super-admin-auth-provider-sso-url"),
    ).toHaveValue("https://idp.example.com/sso");
    // A `<textarea>`, unlike a plain `<input type="text">`, preserves the
    // certificate's embedded newlines instead of collapsing them.
    expect(
      within(form).getByTestId("super-admin-auth-provider-idp-x509-cert"),
    ).toHaveValue(
      "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----",
    );
    expect(
      within(form).getByTestId("super-admin-auth-provider-sp-x509-cert"),
    ).toHaveValue(
      "-----BEGIN CERTIFICATE-----\nMIIC...\n-----END CERTIFICATE-----",
    );
  });

  it("auto-fills the IdP fields from the metadata URL once it's entered", async () => {
    const previewSamlMetadata = vi
      .spyOn(authProvidersService, "previewSamlMetadata")
      .mockResolvedValue({
        idp_entity_id: "https://idp2.example.com/entity",
        sso_url: "https://idp2.example.com/sso",
        sso_binding: "post",
        slo_url: "https://idp2.example.com/slo",
        idp_x509_cert:
          "-----BEGIN CERTIFICATE-----MIID...-----END CERTIFICATE-----",
        name_id_format: "urn:oasis:names:tc:SAML:2.0:nameid-format:transient",
      });
    const user = userEvent.setup();
    const form = await renderEditPage(SAML_PROVIDER);

    const metadataUrlInput = within(form).getByTestId(
      "super-admin-auth-provider-metadata-url",
    );
    await user.clear(metadataUrlInput);
    await user.type(metadataUrlInput, "https://idp2.example.com/metadata");
    await user.tab();

    await waitFor(() =>
      expect(previewSamlMetadata).toHaveBeenCalledWith(
        "https://idp2.example.com/metadata",
      ),
    );
    expect(
      within(form).getByTestId("super-admin-auth-provider-idp-entity-id"),
    ).toHaveValue("https://idp2.example.com/entity");
    expect(
      within(form).getByTestId("super-admin-auth-provider-sso-url"),
    ).toHaveValue("https://idp2.example.com/sso");
    expect(
      within(form).getByTestId("super-admin-auth-provider-sso-binding"),
    ).toHaveValue("SUPER_ADMIN$AUTH_PROVIDER_SSO_BINDING_POST");
    expect(
      within(form).getByTestId("super-admin-auth-provider-slo-url"),
    ).toHaveValue("https://idp2.example.com/slo");
    expect(
      within(form).getByTestId("super-admin-auth-provider-idp-x509-cert"),
    ).toHaveValue(
      "-----BEGIN CERTIFICATE-----MIID...-----END CERTIFICATE-----",
    );
    expect(
      within(form).getByTestId("super-admin-auth-provider-name-id-format"),
    ).toHaveValue("urn:oasis:names:tc:SAML:2.0:nameid-format:transient");

    // Blurring again without changing the URL shouldn't re-fetch.
    await user.click(metadataUrlInput);
    await user.tab();
    expect(previewSamlMetadata).toHaveBeenCalledTimes(1);
  });

  it("shows an inline error when the metadata URL can't be fetched, without blocking manual entry", async () => {
    const previewSamlMetadata = vi
      .spyOn(authProvidersService, "previewSamlMetadata")
      .mockRejectedValue(new Error("Failed to fetch metadata URL: 404"));
    const user = userEvent.setup();
    const form = await renderEditPage(SAML_PROVIDER);

    const metadataUrlInput = within(form).getByTestId(
      "super-admin-auth-provider-metadata-url",
    );
    await user.clear(metadataUrlInput);
    await user.type(metadataUrlInput, "https://idp2.example.com/metadata");
    await user.tab();

    await waitFor(() => expect(previewSamlMetadata).toHaveBeenCalled());
    // The real backend error message is shown when there is one -- the
    // generic METADATA_FETCH_ERROR copy is only a fallback.
    expect(
      within(form).getByTestId("super-admin-auth-provider-metadata-url-error"),
    ).toHaveTextContent("Failed to fetch metadata URL: 404");
    expect(
      within(form).getByTestId("super-admin-auth-provider-form-submit"),
    ).not.toBeDisabled();
  });

  it("renders the metadata URL help text on its own line below the label, not crowded next to it", async () => {
    const form = await renderEditPage(SAML_PROVIDER);

    const hint = within(form).getByTestId(
      "super-admin-auth-provider-metadata-url-hint",
    );
    // `description` renders as its own block between the label row and the
    // `<input>`, rather than being squeezed into the label's own row as
    // `hint` would do.
    const labelRow = within(form)
      .getByText("SUPER_ADMIN$AUTH_PROVIDER_METADATA_URL")
      .closest("div");
    expect(labelRow).not.toContainElement(hint);
  });

  it("shows the SP metadata URL for a SAML provider, copyable to the clipboard", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    const form = await renderEditPage(SAML_PROVIDER);

    const spMetadataUrlInput = within(form).getByTestId(
      "super-admin-auth-provider-sp-metadata-url",
    );
    expect(spMetadataUrlInput).toHaveValue(
      `${window.location.origin}/saml/3/metadata`,
    );
    expect(spMetadataUrlInput).toHaveAttribute("readonly");

    await user.click(
      within(form).getByTestId(
        "super-admin-auth-provider-sp-metadata-url-copy",
      ),
    );
    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/saml/3/metadata`,
    );
  });

  it("shows a read-only SP certificate, with no way to type a private key", async () => {
    const form = await renderEditPage(SAML_PROVIDER);

    expect(
      within(form).getByTestId("super-admin-auth-provider-sp-x509-cert"),
    ).toHaveAttribute("readonly");
    expect(
      within(form).queryByTestId("super-admin-auth-provider-sp-private-key"),
    ).not.toBeInTheDocument();
    expect(
      within(form).queryByTestId(
        "super-admin-auth-provider-sp-x509-cert-pending",
      ),
    ).not.toBeInTheDocument();
  });

  it("downloads the SP certificate as a PEM file", async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.fn().mockReturnValue("blob:mock-sp-certificate");
    const revokeObjectURL = vi.fn();
    Object.assign(window.URL, { createObjectURL, revokeObjectURL });
    const form = await renderEditPage(SAML_PROVIDER);

    await user.click(
      within(form).getByTestId(
        "super-admin-auth-provider-sp-x509-cert-download",
      ),
    );

    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-sp-certificate");
  });

  it("omits details (preserving the SP private key) when only a non-SAML field changes", async () => {
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(SAML_PROVIDER);
    const form = await renderEditPage(SAML_PROVIDER);

    await user.click(
      within(form).getByTestId("super-admin-auth-provider-allow-registration"),
    );
    await user.click(
      within(form).getByTestId("super-admin-auth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateAuthProvider).toHaveBeenCalledWith({
        id: 3,
        title: "Okta SAML",
        is_idp: true,
        icon: null,
        color: DEFAULT_AUTH_PROVIDER_COLOR,
        allow_registration: false,
        allow_match_by_email: true,
      }),
    );
  });

  it("preserves the existing attribute mapping when a different SAML field is edited", async () => {
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(SAML_PROVIDER);
    const form = await renderEditPage(SAML_PROVIDER);

    expect(
      within(form).getByTestId("super-admin-auth-provider-attribute-mapping"),
    ).toHaveValue("email=mail, name=displayName");

    const sloUrlInput = within(form).getByTestId(
      "super-admin-auth-provider-slo-url",
    );
    await user.clear(sloUrlInput);
    await user.type(sloUrlInput, "https://idp.example.com/slo-2");
    await user.click(
      within(form).getByTestId("super-admin-auth-provider-form-submit"),
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

  it.each([
    [
      "SUPER_ADMIN$AUTH_PROVIDER_ALLOW_REGISTRATION_HINT",
      "super-admin-auth-provider-allow-registration",
    ],
    [
      "SUPER_ADMIN$AUTH_PROVIDER_ALLOW_MATCH_BY_EMAIL_HINT",
      "super-admin-auth-provider-allow-match-by-email",
    ],
    [
      "SUPER_ADMIN$AUTH_PROVIDER_IS_IDP_HINT",
      "super-admin-auth-provider-is-idp",
    ],
  ])("explains the %s toggle without toggling it", async (copy, toggleTestId) => {
    const user = userEvent.setup();
    const form = await renderEditPage(GITHUB_PROVIDER);

    const toggle = within(form).getByTestId(toggleTestId) as HTMLInputElement;
    const checkedBefore = toggle.checked;
    const hintIcon = within(toggle.closest("label")!).getByLabelText(copy);
    expect(hintIcon).toBeInTheDocument();

    await user.click(hintIcon);
    expect(toggle.checked).toBe(checkedBefore);
  });

  it("sends details wholesale when a SAML field is changed", async () => {
    const user = userEvent.setup();
    const updateAuthProvider = vi
      .spyOn(authProvidersService, "updateAuthProvider")
      .mockResolvedValue(SAML_PROVIDER);
    const form = await renderEditPage(SAML_PROVIDER);

    const ssoUrlInput = within(form).getByTestId(
      "super-admin-auth-provider-sso-url",
    );
    await user.clear(ssoUrlInput);
    await user.type(ssoUrlInput, "https://idp.example.com/sso-2");
    await user.click(
      within(form).getByTestId("super-admin-auth-provider-form-submit"),
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
});
