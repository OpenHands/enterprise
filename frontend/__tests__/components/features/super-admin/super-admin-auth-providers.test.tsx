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
import { oauthProvidersService } from "#/api/oauth-providers-service/oauth-providers-service.api";
import type { OAuthProvider } from "#/api/oauth-providers-service/oauth-providers.types";
import { SuperAdminOAuthProviders } from "#/components/features/super-admin/super-admin-oauth-providers";
import translations from "#/i18n/translation.json";

const GITHUB_PROVIDER: OAuthProvider = {
  id: 1,
  provider_category: "github",
  display_name: "GitHub",
  is_idp: false,
  client_id: "github-client-id",
  has_client_secret: true,
  authorization_url: null,
  token_url: null,
  userinfo_url: null,
  scopes: ["repo"],
  permitted_drift_seconds: 60,
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

function renderOAuthProvidersPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const RouterStub = createRoutesStub([
    {
      path: "/super-admin/oauth-providers",
      Component: SuperAdminOAuthProviders,
    },
  ]);
  render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>
        <RouterStub initialEntries={["/super-admin/oauth-providers"]} />
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
          SUPER_ADMIN$DELETE_OAUTH_PROVIDER_CONFIRM:
            translations.SUPER_ADMIN$DELETE_OAUTH_PROVIDER_CONFIRM.en,
        },
      },
    },
    interpolation: { escapeValue: false },
  });
});

describe("Super Admin OAuth Providers page", () => {
  beforeEach(() => {
    vi.spyOn(oauthProvidersService, "listOAuthProviders").mockResolvedValue([
      GITHUB_PROVIDER,
    ]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists configured OAuth providers", async () => {
    renderOAuthProvidersPage();

    expect(await screen.findByText("GitHub")).toBeInTheDocument();
    expect(screen.getByText("github-client-id")).toBeInTheDocument();
    expect(
      screen.getByText("SUPER_ADMIN$OAUTH_PROVIDER_SECRET_SET"),
    ).toBeInTheDocument();
  });

  it("defaults the category dropdown to Enterprise SSO when adding a provider", async () => {
    const user = userEvent.setup();
    renderOAuthProvidersPage();
    await screen.findByText("GitHub");

    await user.click(
      screen.getByRole("button", { name: "SUPER_ADMIN$ADD_OAUTH_PROVIDER" }),
    );
    const dialog = screen.getByTestId("super-admin-oauth-provider-form");

    expect(
      within(dialog).getByTestId("super-admin-oauth-provider-category"),
    ).toHaveValue("Enterprise SSO");
  });

  it("creates a new OAuth provider from the form", async () => {
    const user = userEvent.setup();
    const createOAuthProvider = vi
      .spyOn(oauthProvidersService, "createOAuthProvider")
      .mockResolvedValue({ ...GITHUB_PROVIDER, id: 2, display_name: "GitLab" });
    renderOAuthProvidersPage();
    await screen.findByText("GitHub");

    await user.click(
      screen.getByRole("button", { name: "SUPER_ADMIN$ADD_OAUTH_PROVIDER" }),
    );
    const dialog = screen.getByTestId("super-admin-oauth-provider-form");
    const categoryInput = within(dialog).getByTestId(
      "super-admin-oauth-provider-category",
    );
    await user.clear(categoryInput);
    await user.type(categoryInput, "gitlab");
    await user.type(
      within(dialog).getByTestId("super-admin-oauth-provider-display-name"),
      "GitLab",
    );
    await user.type(
      within(dialog).getByTestId("super-admin-oauth-provider-client-id"),
      "gitlab-client-id",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-oauth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(createOAuthProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          provider_category: "gitlab",
          display_name: "GitLab",
          client_id: "gitlab-client-id",
        }),
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByTestId("super-admin-oauth-provider-form"),
      ).not.toBeInTheDocument(),
    );
  });

  it("sends the provider_category key, not its label, when picked from the dropdown", async () => {
    const user = userEvent.setup();
    const createOAuthProvider = vi
      .spyOn(oauthProvidersService, "createOAuthProvider")
      .mockResolvedValue({
        ...GITHUB_PROVIDER,
        id: 2,
        provider_category: "integrated_idp",
        display_name: "Password Login",
        is_idp: true,
      });
    renderOAuthProvidersPage();
    await screen.findByText("GitHub");

    await user.click(
      screen.getByRole("button", { name: "SUPER_ADMIN$ADD_OAUTH_PROVIDER" }),
    );
    const dialog = screen.getByTestId("super-admin-oauth-provider-form");
    const categoryInput = within(dialog).getByTestId(
      "super-admin-oauth-provider-category",
    );
    // Open the list and click the option by its visible label, exactly as a
    // user would -- this is what previously left `provider_category` set to
    // the English label ("Integrated (password) IDP") instead of the
    // "integrated_idp" key, because the autocomplete syncs its input text to
    // the selected item's label after selection.
    await user.click(categoryInput);
    await user.click(
      await screen.findByRole("option", {
        name: "Integrated (password) IDP",
      }),
    );
    await user.type(
      within(dialog).getByTestId("super-admin-oauth-provider-display-name"),
      "Password Login",
    );
    await user.type(
      within(dialog).getByTestId("super-admin-oauth-provider-client-id"),
      "integrated-idp",
    );
    await user.click(
      within(dialog).getByTestId("super-admin-oauth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(createOAuthProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          provider_category: "integrated_idp",
        }),
      ),
    );
  });

  it("keeps the existing provider_category key when editing without touching the dropdown", async () => {
    const user = userEvent.setup();
    const updateOAuthProvider = vi
      .spyOn(oauthProvidersService, "updateOAuthProvider")
      .mockResolvedValue(GITHUB_PROVIDER);
    renderOAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-oauth-provider-actions-1"),
    );
    await user.click(screen.getByTestId("super-admin-oauth-provider-edit-1"));

    const dialog = screen.getByTestId("super-admin-oauth-provider-form");
    expect(
      within(dialog).getByTestId("super-admin-oauth-provider-category"),
    ).toHaveValue("GitHub");

    await user.click(
      within(dialog).getByTestId("super-admin-oauth-provider-form-submit"),
    );

    await waitFor(() =>
      expect(updateOAuthProvider).toHaveBeenCalledWith(
        expect.objectContaining({ id: 1, provider_category: "github" }),
      ),
    );
  });

  it("asks for confirmation naming the provider before deleting it", async () => {
    const user = userEvent.setup();
    const deleteOAuthProvider = vi
      .spyOn(oauthProvidersService, "deleteOAuthProvider")
      .mockResolvedValue(undefined);
    renderOAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-oauth-provider-actions-1"),
    );
    await user.click(screen.getByTestId("super-admin-oauth-provider-delete-1"));

    const dialog = screen.getByTestId(
      "super-admin-oauth-provider-delete-confirm",
    );
    expect(dialog).toHaveTextContent("GitHub");
    expect(deleteOAuthProvider).not.toHaveBeenCalled();

    await user.click(
      within(dialog).getByTestId(
        "super-admin-oauth-provider-delete-confirm-submit",
      ),
    );

    await waitFor(() =>
      expect(deleteOAuthProvider).toHaveBeenCalledWith({ id: 1 }),
    );
  });

  it("keeps the provider when the delete is cancelled", async () => {
    const user = userEvent.setup();
    const deleteOAuthProvider = vi
      .spyOn(oauthProvidersService, "deleteOAuthProvider")
      .mockResolvedValue(undefined);
    renderOAuthProvidersPage();

    await user.click(
      await screen.findByTestId("super-admin-oauth-provider-actions-1"),
    );
    await user.click(screen.getByTestId("super-admin-oauth-provider-delete-1"));
    await user.click(
      screen.getByTestId("super-admin-oauth-provider-delete-confirm-cancel"),
    );

    expect(
      screen.queryByTestId("super-admin-oauth-provider-delete-confirm"),
    ).not.toBeInTheDocument();
    expect(deleteOAuthProvider).not.toHaveBeenCalled();
    expect(screen.getByText("GitHub")).toBeInTheDocument();
  });
});
